#!/usr/bin/env python
# -*- coding:utf-8 -*-
from __future__ import absolute_import
from __future__ import unicode_literals
from __future__ import division
from __future__ import print_function

from typing import List, Optional
import torch
from torch import nn
from torch.nn import functional as F


class SimpleEncoder(nn.Module):

    def __init__(self, n_dim_emb: int, n_digits: int, n_ary: int,
                 n_dim_hidden: Optional[int] = None, f_temperature: float = 1.0):

        super(SimpleEncoder, self).__init__()

        self._tau = f_temperature
        self._n_dim_emb = n_dim_emb
        self._n_dim_hidden = int(n_digits*n_ary //2) if n_dim_hidden is None else n_dim_hidden
        self._n_digits = n_digits
        self._n_ary = n_ary

        self._build()

    def _build(self):

        self.x_to_h = nn.Linear(in_features=self._n_dim_emb, out_features=self._n_dim_hidden)
        self.lst_h_to_z = nn.ModuleList([nn.Linear(in_features=self._n_dim_hidden, out_features=self._n_ary) for n in range(self._n_digits)])

    def forward(self, input_x: torch.Tensor):

        t_h = torch.tanh(self.x_to_h(input_x))
        lst_z = [torch.log(F.softplus(h_to_z(t_h))) for h_to_z in self.lst_h_to_z]
        lst_prob_c = [F.softmax(t_z, dim=1) for t_z in lst_z]
        # lst_c = [F.gumbel_softmax(torch.log(t_prob_c), tau=self._tau) for t_prob_c in lst_prob_c]
        lst_c = [F.gumbel_softmax(t_z, tau=self._tau) for t_z in lst_z]

        t_c = torch.stack(lst_c, dim=1)
        t_prob_c = torch.stack(lst_prob_c, dim=1)

        return t_c, t_prob_c


class CodeLengthAwareEncoder(nn.Module):

    def __init__(self, n_dim_emb: int, n_digits: int, n_ary: int,
                 n_dim_hidden: Optional[int] = None, f_temperature: float = 1.0):

        super(CodeLengthAwareEncoder, self).__init__()
