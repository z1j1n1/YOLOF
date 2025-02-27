from typing import List

import torch
import torch.nn as nn

from cvpods.layers import ShapeSpec
from cvpods.modeling.nn_utils import weight_init

from .utils import get_activation, get_norm

"""
Dilated Encoder HERE
三个使用cspdarknet53 backbone的ENCODER参数相同
添加到YOLOv5需要确定的细节
(0) 替换SPP还是P5->Dilated Encoder->Detect
(1) 使用BLOCK_DILATIONS的值(不同backbone使用的不同)
"""

class DilatedEncoder(nn.Module):
    """
    Dilated Encoder for YOLOF.

    This module contains two types of components:
        - the original FPN lateral convolution layer and fpn convolution layer,
          which are 1x1 conv + 3x3 conv
        - the dilated residual block
    """

    def __init__(self, cfg, input_shape: List[ShapeSpec]):
        super(DilatedEncoder, self).__init__()
        # fmt: off
        self.backbone_level = cfg.MODEL.YOLOF.ENCODER.IN_FEATURES #["res5"]
        self.in_channels = input_shape[self.backbone_level[0]].channels #1024?
        self.encoder_channels = cfg.MODEL.YOLOF.ENCODER.NUM_CHANNELS #512
        self.block_mid_channels = cfg.MODEL.YOLOF.ENCODER.BLOCK_MID_CHANNELS #128
        self.num_residual_blocks = cfg.MODEL.YOLOF.ENCODER.NUM_RESIDUAL_BLOCKS #8
        self.block_dilations = cfg.MODEL.YOLOF.ENCODER.BLOCK_DILATIONS #[1,2,3,4,5,6,7,8]
        self.norm_type = cfg.MODEL.YOLOF.ENCODER.NORM #"SyncBN"
        self.act_type = cfg.MODEL.YOLOF.ENCODER.ACTIVATION #"LeakyReLU"
        # fmt: on
        assert len(self.block_dilations) == self.num_residual_blocks

        # init
        self._init_layers()
        self._init_weight()

    def _init_layers(self):
        self.lateral_conv = nn.Conv2d(self.in_channels,
                                      self.encoder_channels,
                                      kernel_size=1)
        self.lateral_norm = get_norm(self.norm_type, self.encoder_channels)
        self.fpn_conv = nn.Conv2d(self.encoder_channels,
                                  self.encoder_channels,
                                  kernel_size=3,
                                  padding=1)
        self.fpn_norm = get_norm(self.norm_type, self.encoder_channels)
        encoder_blocks = []
        for i in range(self.num_residual_blocks):
            dilation = self.block_dilations[i]
            encoder_blocks.append(
                Bottleneck(
                    self.encoder_channels,
                    self.block_mid_channels,
                    dilation=dilation,
                    norm_type=self.norm_type,
                    act_type=self.act_type
                )
            )
        self.dilated_encoder_blocks = nn.Sequential(*encoder_blocks)

    def _init_weight(self):
        weight_init.c2_xavier_fill(self.lateral_conv)
        weight_init.c2_xavier_fill(self.fpn_conv)
        for m in [self.lateral_norm, self.fpn_norm]:
            nn.init.constant_(m.weight, 1)
            nn.init.constant_(m.bias, 0)
        for m in self.dilated_encoder_blocks.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.normal_(m.weight, mean=0, std=0.01)
                if hasattr(m, 'bias') and m.bias is not None:
                    nn.init.constant_(m.bias, 0)

            if isinstance(m, (nn.GroupNorm, nn.BatchNorm2d, nn.SyncBatchNorm)):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

    def forward(self, feature: torch.Tensor) -> torch.Tensor:
        out = self.lateral_norm(self.lateral_conv(feature))
        out = self.fpn_norm(self.fpn_conv(out))
        return self.dilated_encoder_blocks(out)


class Bottleneck(nn.Module):

    def __init__(self,
                 in_channels: int = 512,
                 mid_channels: int = 128,
                 dilation: int = 1,
                 norm_type: str = 'BN',
                 act_type: str = 'ReLU'):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Sequential(
            nn.Conv2d(in_channels, mid_channels, kernel_size=1, padding=0),
            get_norm(norm_type, mid_channels),
            get_activation(act_type)
        )
        self.conv2 = nn.Sequential(
            nn.Conv2d(mid_channels, mid_channels,
                      kernel_size=3, padding=dilation, dilation=dilation),
            get_norm(norm_type, mid_channels),
            get_activation(act_type)
        )
        self.conv3 = nn.Sequential(
            nn.Conv2d(mid_channels, in_channels, kernel_size=1, padding=0),
            get_norm(norm_type, in_channels),
            get_activation(act_type)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        out = self.conv1(x)
        out = self.conv2(out)
        out = self.conv3(out)
        out = out + identity
        return out


def build_encoder(cfg, input_shape: ShapeSpec):
    return DilatedEncoder(cfg, input_shape=input_shape)
