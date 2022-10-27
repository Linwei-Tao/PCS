'''
Pytorch implementation of fully_searched resnet.

Reference:
[1] S. Zagoruyko and N. Komodakis. Wide residual networks. arXiv preprint arXiv:1605.07146, 2016.
'''

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from typing import Union, List
from utils import gumbel_like
from utils import gumbel_softmax_v1 as gumbel_softmax


def conv3x3(in_planes, out_planes, stride=1):
    " 3x3 convolution with padding "
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride, padding=1, bias=False)


class BasicBlock(nn.Module):
    expansion=1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class Wide_ResNet_Cifar(nn.Module):

    def __init__(self, block, layers, wfactor, num_classes=10, temp=1.0, criterion=None, tau=0.1, weight_root=None):
        super(Wide_ResNet_Cifar, self).__init__()
        self.inplanes = 16
        self.conv1 = nn.Conv2d(3, 16, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(16)
        self.relu = nn.ReLU(inplace=True)
        self.layer1 = self._make_layer(block, 16*wfactor, layers[0])
        self.layer2 = self._make_layer(block, 32*wfactor, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 64*wfactor, layers[2], stride=2)
        self.avgpool = nn.AvgPool2d(8, stride=1)
        self.fc = nn.Linear(64*block.expansion*wfactor, num_classes)
        self.temp = temp
        self.block_names = ['layer1','layer2','layer3','fc']


        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

        self._tau = tau
        self.weight_root = weight_root
        self.model_name = "wide_resnet"
        self._initialize_alphas()


    def _initialize_alphas(self):
        # number of layers
        k = 4
        # number of candidates
        num_ops = 350

        # init architecture parameters alpha
        self.alphas = (1e-3 * torch.randn(k, num_ops)).to('cuda').requires_grad_(True)
        # init Gumbel distribution for Gumbel softmax sampler
        self.gumbel = gumbel_like(self.alphas)

    def arch_weights(self, smooth=False, cat: bool=True) -> Union[List[torch.tensor], torch.tensor]:
        weights = gumbel_softmax(self.alphas, tau=self._tau, dim=-1, g=self.gumbel)
        if cat:
            return torch.cat(weights)
        else:
            return weights

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes * block.expansion)
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for _ in range(1, int(blocks)):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)

        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x) / self.temp

        return x


    def load_combination(self, index):

        saved_model_name = "{}/{}_cross_entropy_{}.model".format(self.weight_root, self.model_name, 350)
        model_dict = torch.load(str(saved_model_name))
        self.load_state_dict(model_dict, strict=False)

        for block_name in self.block_names:
            idx = self.block_names.index(block_name)
            saved_model_name = "{}/{}_cross_entropy_{}.model".format(self.weight_root, self.model_name, self.ops[index[idx]] + 1)
            model_dict = torch.load(str(saved_model_name))
            if block_name == 'fc':
                modified_dict = {k[3:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            else:
                modified_dict = {k[7:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            getattr(self, block_name).load_state_dict(model_dict, strict=False)


        return self.ops[index.cpu()]

    def load_combination_weight(self, combination, weight_folder, model_name):
        self.weight_root=weight_folder
        saved_model_name = "{}/{}_cross_entropy_{}.model".format(self.weight_root, self.model_name, 350)
        model_dict = torch.load(str(saved_model_name))
        self.load_state_dict(model_dict, strict=False)

        for block_name in self.block_names:
            idx = self.block_names.index(block_name)
            saved_model_name = "{}/{}_cross_entropy_{}.model".format(self.weight_root, self.model_name, combination[0] + 1)
            model_dict = torch.load(str(saved_model_name))
            if block_name == 'fc':
                modified_dict = {k[3:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            else:
                modified_dict = {k[7:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            getattr(self, block_name).load_state_dict(model_dict, strict=False)


    def load_gumbel_weight(self):
        weights = self.arch_weights(cat=False)
        combination = torch.argmax(weights, -1)

        saved_model_name = "{}/{}_cross_entropy_{}.model".format(self.weight_root, self.model_name, combination[0] + 1)
        model_dict = torch.load(str(saved_model_name))
        self.load_state_dict(model_dict, strict=True)

        for block_name in self.block_names:
            idx = self.block_names.index(block_name)
            saved_model_name = "{}/{}_cross_entropy_{}.model".format(self.weight_root, self.model_name,
                                                                     combination[idx] + 1)
            model_dict = torch.load(str(saved_model_name))
            if block_name == 'fc':
                modified_dict = {k[3:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            else:
                modified_dict = {k[7:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            getattr(self, block_name).load_state_dict(model_dict, strict=False)

        return combination

    def arch_parameters(self) -> List[torch.tensor]:
        return [self.alphas]

def wide_resnet_cifar(temp=1.0, num_classes=10, depth=26, width=10, **kwargs):
    assert (depth - 2) % 6 == 0
    n = (depth - 2) / 6
    return Wide_ResNet_Cifar(BasicBlock, [n, n, n], width, num_classes=num_classes, temp=temp, **kwargs)