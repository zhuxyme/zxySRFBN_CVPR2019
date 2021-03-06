import torch
import torch.nn as nn
from .blocks import ConvBlock, DeconvBlock, MeanShift
from networks import gcnet

class FeedbackBlock(nn.Module):
    def __init__(self, num_features, num_groups, upscale_factor, act_type, norm_type):
        super(FeedbackBlock, self).__init__()
        if upscale_factor == 2:
            stride = 2
            padding = 2
            kernel_size = 6
        elif upscale_factor == 3:
            stride = 3
            padding = 2
            kernel_size = 7
        elif upscale_factor == 4:
            stride = 4
            padding = 2
            kernel_size = 8
        elif upscale_factor == 8:
            stride = 8
            padding = 2
            kernel_size = 12

        self.num_groups = num_groups

        self.compress_in = ConvBlock(2*num_features, num_features,
                                     kernel_size=1,
                                     act_type=act_type, norm_type=norm_type)
        self.compress_in3 = ConvBlock(3*num_features, num_features,
                                     kernel_size=1,
                                     act_type=act_type, norm_type=norm_type)
        self.compress_in4 = ConvBlock(4*num_features, num_features,
                                     kernel_size=1,
                                     act_type=act_type, norm_type=norm_type)

        self.upBlocks = nn.ModuleList()
        self.downBlocks = nn.ModuleList()
        self.uptranBlocks = nn.ModuleList()
        self.downtranBlocks = nn.ModuleList()

        for idx in range(self.num_groups):
            self.upBlocks.append(DeconvBlock(num_features, num_features,
                                             kernel_size=kernel_size, stride=stride, padding=padding,
                                             act_type=act_type, norm_type=norm_type))
            self.downBlocks.append(ConvBlock(num_features, num_features,
                                             kernel_size=kernel_size, stride=stride, padding=padding,
                                             act_type=act_type, norm_type=norm_type, valid_padding=False))
            if idx > 0:
                self.uptranBlocks.append(ConvBlock(num_features*(idx+1), num_features,
                                                   kernel_size=1, stride=1,
                                                   act_type=act_type, norm_type=norm_type))
                self.downtranBlocks.append(ConvBlock(num_features*(idx+1), num_features,
                                                     kernel_size=1, stride=1,
                                                     act_type=act_type, norm_type=norm_type))

        self.compress_out = ConvBlock(num_groups*num_features, num_features,
                                      kernel_size=1,
                                      act_type=act_type, norm_type=norm_type)

        self.should_reset = True
        self.last_hidden = None
        self.last_hidden_list=[]



    def forward(self, x,_):
        if self.should_reset:#第一次的时候设置为true
            self.last_hidden = torch.zeros(x.size()).cuda()#在计算multi-adds的时候应该去掉.cuda()
            #self.last_hidden = torch.zeros(x.size())
            self.last_hidden.copy_(x)
            self.last_hidden_list.append(self.last_hidden)
            self.should_reset = False

        # x = torch.cat((x, self.last_hidden), dim=1)#图3中1*1卷积的前面的输入部分
        # x = self.compress_in(x)#就是经过一个1*1的卷积
        if _ ==0:
            x = torch.cat((x, self.last_hidden), dim=1)
            x = self.compress_in(x)
        if _ ==1:
            x = torch.cat((x, torch.cat(self.last_hidden_list[1:_ + 1], dim=1)), dim=1)
            x = self.compress_in(x)
            # print("2222222222222222222", x.shape, _)
        elif _ ==2:
            x = torch.cat((x, torch.cat(self.last_hidden_list[1:_ + 1], dim=1)), dim=1)
            x = self.compress_in3(x)
            # print("333333333333333333", x.shape, _)
        elif _==3:
            x = torch.cat((x, torch.cat(self.last_hidden_list[1:_ + 1], dim=1)), dim=1)
            x=self.compress_in4(x)
            # print("4444444444444444444", x.shape, _)

        lr_features = []
        hr_features = []
        lr_features.append(x)

        for idx in range(self.num_groups):
            LD_L = torch.cat(tuple(lr_features), 1)    # when idx == 0, lr_features == [x]
            if idx > 0:
                LD_L = self.uptranBlocks[idx-1](LD_L)#上采样之前的卷积
            LD_H = self.upBlocks[idx](LD_L)#反卷积，上采样

            hr_features.append(LD_H)

            LD_H = torch.cat(tuple(hr_features), 1)
            if idx > 0:
                LD_H = self.downtranBlocks[idx-1](LD_H)#下采样之前的卷积
            LD_L = self.downBlocks[idx](LD_H)#下采样

            lr_features.append(LD_L)

        del hr_features
        output = torch.cat(tuple(lr_features[1:]), 1)   # leave out input x, i.e. lr_features[0]
        output = self.compress_out(output)

        self.last_hidden_list.append(output)#反馈信息的列表
        if _ ==3:#如果当前的反馈信息达到迭代数代数的值，清空列表
            self.last_hidden_list.clear()

        self.last_hidden = output

        return output

    def reset_state(self):
        self.should_reset = True

class ZXYNET(nn.Module):
    def __init__(self, in_channels, out_channels, num_features, num_steps, num_groups, upscale_factor, act_type = 'prelu', norm_type = None):
        super(ZXYNET, self).__init__()

        if upscale_factor == 2:
            stride = 2
            padding = 2
            kernel_size = 6
        elif upscale_factor == 3:
            stride = 3
            padding = 2
            kernel_size = 7
        elif upscale_factor == 4:
            stride = 4
            padding = 2
            kernel_size = 8
        elif upscale_factor == 8:
            stride = 8
            padding = 2
            kernel_size = 12

        self.num_steps = num_steps
        self.num_features = num_features
        self.upscale_factor = upscale_factor

        # RGB mean for DIV2K
        rgb_mean = (0.4488, 0.4371, 0.4040)
        rgb_std = (1.0, 1.0, 1.0)
        self.sub_mean = MeanShift(rgb_mean, rgb_std)

        # LR feature extraction block
        self.conv_in = ConvBlock(in_channels, 4*num_features,
                                 kernel_size=3,
                                 act_type=act_type, norm_type=norm_type)
        self.feat_in = ConvBlock(4*num_features, num_features,
                                 kernel_size=1,
                                 act_type=act_type, norm_type=norm_type)

        # basic block
        self.block = FeedbackBlock(num_features, num_groups, upscale_factor, act_type, norm_type)

        # reconstruction block
		# uncomment for pytorch 0.4.0
        # self.upsample = nn.Upsample(scale_factor=upscale_factor, mode='bilinear')

        self.out = DeconvBlock(num_features, num_features,
                               kernel_size=kernel_size, stride=stride, padding=padding,
                               act_type='prelu', norm_type=norm_type)
        self.conv_out = ConvBlock(num_features, out_channels,
                                  kernel_size=3,
                                  act_type=None, norm_type=norm_type)

        self.add_mean = MeanShift(rgb_mean, rgb_std, 1)
        self.gcnet=gcnet.ContextBlock2d(inplanes=3)

    def forward(self, x):
        self._reset_state()#设置self.should_reset = True

        x = self.sub_mean(x)#print(x.shape)  torch.Size([16, 3, 40, 40])
		# uncomment for pytorch 0.4.0
        # inter_res = self.upsample(x)

		# comment for pytorch 0.4.0
        inter_res = nn.functional.interpolate(x, scale_factor=self.upscale_factor, mode='bilinear', align_corners=False)
        #这里的就是upasmple模块，还是没有采用pixshuffle模块

        # LR feature extraction block，就是两个卷积，做初始特征提取部分
        x = self.conv_in(x)#这里是先激活，然后经过一个卷积
        x = self.feat_in(x)

        outs = []
        for _ in range(self.num_steps):
            h = self.block(x,_)#FeedbackBlock部分

            h = torch.add(inter_res, self.gcnet(self.conv_out(self.out(h))))
            # h = torch.add(inter_res, self.conv_out(self.out(h)))
            h = self.add_mean(h)
            outs.append(h)
        #print('输出的长度',len(outs))
        return outs# return output of every timesteps


    def _reset_state(self):
        self.block.reset_state()