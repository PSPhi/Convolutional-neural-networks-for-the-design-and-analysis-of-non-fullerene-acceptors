import torch
from torch import nn
import torch.nn.functional as F
from torch.nn.utils import weight_norm


class ConvLayer(nn.Module):
    def __init__(self, n_inputs, n_outputs, kernel_size, stride, dilation, padding, dropout=0.2):
        super(ConvLayer, self).__init__()
        self.conv = weight_norm(nn.Conv1d(n_inputs, n_outputs * 2, kernel_size,
                              stride=stride, padding=padding, dilation=dilation))
        self.dilation = dilation
        self.padding = padding
        self.glu = nn.GLU(dim=1)
        self.dropout = nn.Dropout(dropout)
        self.trans = nn.Conv1d(n_inputs, n_outputs, 1) if n_inputs != n_outputs else None
        self.init_weights()

    def init_weights(self):
        self.conv.weight.data.normal_(0, 0.01)
        if self.trans is not None:
            self.trans.weight.data.normal_(0, 0.01)

    def forward(self, x):
        y = self.conv(x)
        out = self.glu(y[:,:,:-self.padding].contiguous()) if self.dilation!=self.padding else self.glu(y)
        out = self.dropout(out)
        if self.trans is not None:
            x = self.trans(x)
        return out + x


class GenEncoder(nn.Module):
    def __init__(self, input_size,hid_size, n_levels,kernel_size=3, dropout=0.2):
        super(GenEncoder, self).__init__()
        layers = []
        for i in range(n_levels):
            dilation_size = 2 ** i
            if i==0:
                layers += [ConvLayer(input_size, hid_size, kernel_size, stride=1, dilation=dilation_size,
                                     padding=(kernel_size - 1) * dilation_size, dropout=dropout)]
            else:
                layers += [ConvLayer(hid_size, hid_size, kernel_size, stride=1, dilation=dilation_size,
                                 padding=(kernel_size - 1) * dilation_size, dropout=dropout)]

        self.network = nn.Sequential(*layers)

    def forward(self, x):
        return self.network(x)


class GEN(nn.Module):
    def __init__(self, input_size, dic_size, output_size, hid_size=256, n_levels=5, kernel_size=3, emb_dropout=0.1, dropout=0.2):
        super(GEN, self).__init__()
        self.emb = nn.Embedding(dic_size, input_size)
        self.drop = nn.Dropout(emb_dropout)
        self.linear = nn.Linear(input_size, hid_size,bias=False)
        self.encoder = GenEncoder( input_size,hid_size, n_levels, kernel_size, dropout=dropout)
        self.decoder = nn.Linear(hid_size, output_size)
        self.init_weights()

    def init_weights(self):
        self.emb.weight.data.uniform_(-0.1, 0.1)

    def forward(self, input):
        emb = self.drop(self.emb(input))
        y = self.encoder(emb.transpose(1, 2))
        o = self.decoder(y.transpose(1, 2))
        return o.contiguous()


class PreEncoder(nn.Module):
    def __init__(self, num_inputs, num_channels, kernel_size=3, dropout=0.2):
        super(PreEncoder, self).__init__()
        layers = []
        num_levels = len(num_channels)
        for i in range(num_levels):
            dilation_size = 2 ** i
            in_channels = num_inputs if i == 0 else num_channels[i - 1]
            out_channels = num_channels[i]
            layers += [ConvLayer(in_channels, out_channels, kernel_size, stride=1,
                                 dilation=dilation_size,padding=dilation_size, dropout=dropout)]

        self.network = nn.Sequential(*layers)

    def forward(self, x):
        return self.network(x)


class NNet(nn.Module):
    def __init__(self, n_in, n_out, hide=(64, 64, 8)):
        super(NNet, self).__init__()
        self.n_hide = len(hide)
        self.fcs = nn.ModuleList([weight_norm(nn.Linear(n_in, hide[i] * 2)) if i == 0 else
                                  weight_norm(nn.Linear(hide[i - 1], n_out)) if i == self.n_hide else
                                  weight_norm(nn.Linear(hide[i - 1], hide[i] * 2)) for i in range(self.n_hide + 1)])
        self.init_weights()

    def init_weights(self):
        for i in range(self.n_hide + 1):
            self.fcs[i].weight.data.normal_(0, 0.01)

    def forward(self, x):
        for i in range(self.n_hide):
            x = F.glu(self.fcs[i](x))
        x = self.fcs[-1](x)
        return x


class PreDecoder(nn.Module):
    def __init__(self, emb_size, encoder_out, output_size):
        super(PreDecoder, self).__init__()
        self.linear0 = NNet(n_in=emb_size, n_out=output_size, hide=(encoder_out * 2, encoder_out * 2, encoder_out))
        self.linear1 = weight_norm(nn.Linear(encoder_out, output_size))
        self.softmax = nn.Softmax(dim=1)
        self.atten = None
        self.init_weights()

    def init_weights(self):
        self.linear1.weight.data.normal_(0, 0.01)

    def forward(self, emb, v):
        h = self.linear0(emb)
        v = self.linear1(v)
        a = self.softmax((v * h).masked_fill(emb[:,:,:1] == 0, float('-inf')))
        self.atten = a
        out = torch.sum(a * h, 1)
        return out


class PRE(nn.Module):
    def __init__(self, input_size, dic_size, output_size, num_channels, kernel_size=3, emb_dropout=0.1, dropout=0.2):
        super(PRE, self).__init__()
        self.emb = nn.Embedding(dic_size, input_size, padding_idx=0)
        self.drop = nn.Dropout(emb_dropout)
        self.encoder = PreEncoder(input_size, num_channels, kernel_size, dropout=dropout)
        self.decoder = PreDecoder(input_size, num_channels[-1], output_size)
        self.init_weights()

    def init_weights(self):
        self.emb.weight.data.uniform_(-0.1, 0.1)

    def forward(self, input):
        emb = self.drop(self.emb(input))
        v = self.encoder(emb.transpose(1, 2))
        o = self.decoder(emb, v.transpose(1, 2))
        return o.contiguous()