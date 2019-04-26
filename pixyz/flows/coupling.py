import torch
import numpy as np

from .flows import Flow


class AffineCouplingLayer(Flow):
    r"""
    Affine coupling layer

    .. math::
        :nowrap:

        \begin{eqnarray*}
        \mathbf{y}_{1:d} &=& \mathbf{x}_{1:d} \\
        \mathbf{y}_{d+1:D} &=& \mathbf{x}_{d+1:D} \odot \exp(s(\mathbf{x}_{1:d})+t(\mathbf{x}_{1:d}))
        \end{eqnarray*}

    """

    def __init__(self, in_channels, mask_type="channel_wise",
                 scale_net=None, translate_net=None, scale_translate_net=None,
                 inverse_mask=False):
        super().__init__(in_channels)

        # mask initializations
        if mask_type in ["checkerboard", "channel_wise"]:
            self.mask_type = mask_type
        else:
            raise ValueError

        self.inverse_mask = inverse_mask

        self.scale_net = None
        self.translate_net = None
        self.scale_translate_net = None

        if scale_net and translate_net:
            self.scale_net = scale_net
            self.translate_net = translate_net
        elif scale_translate_net:
            self.scale_translate_net = scale_translate_net
        else:
            raise ValueError

    def build_mask(self, x):
        """
        Parameters
        ----------
        x : torch.Tensor

        Returns
        -------
        mask : torch.tensor

        Examples
        --------
        >>> f1 = AffineCouplingLayer(4, mask_type="channel_wise", scale_net=lambda x: x, translate_net=lambda x: x,
        ...                          inverse_mask=False)
        >>> x1 = torch.randn([1,4,3,3])
        >>> f1.build_mask(x1)
        tensor([[[[1.]],
        <BLANKLINE>
                 [[1.]],
        <BLANKLINE>
                 [[0.]],
        <BLANKLINE>
                 [[0.]]]])
        >>> f2 = AffineCouplingLayer(2, mask_type="checkerboard", scale_net=lambda x: x, translate_net=lambda x: x,
        ...                          inverse_mask=True)
        >>> x2 = torch.randn([1,2,5,5])
        >>> f2.build_mask(x2)
        tensor([[[[0., 1., 0., 1., 0.],
                  [1., 0., 1., 0., 1.],
                  [0., 1., 0., 1., 0.],
                  [1., 0., 1., 0., 1.],
                  [0., 1., 0., 1., 0.]]]])

        """
        if x.dim() == 4:
            [_, channels, height, width] = x.shape
            if self.mask_type == "checkerboard":
                mask = checkerboard_mask(height, width, self.inverse_mask)
                return torch.from_numpy(mask).view(1, 1, height, width).to(x.device)
            else:
                mask = channel_wise_mask(channels, self.inverse_mask)
                return torch.from_numpy(mask).view(1, channels, 1, 1).to(x.device)

        elif x.dim() == 2:
            [_, n_features] = x.shape
            if self.mask_type != "checkerboard":
                mask = channel_wise_mask(n_features, self.inverse_mask)
                return torch.from_numpy(mask).view(1, n_features).to(x.device)

        raise ValueError

    def get_parameters(self, x, y=None):
        """
        Parameters
        ----------
        x : torch.tensor
        y : torch.tensor

        Returns
        -------
        s : torch.tensor
        t : torch.tensor

        Examples
        --------
        >>> # Channel-wise mask
        >>> f1 = AffineCouplingLayer(4, mask_type="channel_wise", scale_net=lambda x: x, translate_net=lambda x: x,
        ...                          inverse_mask=False)
        >>> x1 = torch.randn([1,4,3,3])
        >>> log_s, t = f1.get_parameters(x1)
        >>> print(torch.sum(log_s[:, :2, :, :]).data)
        tensor(0.)
        >>> # Checkerboard mask
        >>> f2 = AffineCouplingLayer(2, mask_type="checkerboard", scale_net=lambda x: x, translate_net=lambda x: x,
        ...                          inverse_mask=True)
        >>> x2 = torch.randn([1,2,5,5])
        >>> log_s, t = f2.get_parameters(x2)
        >>> print(torch.sum(log_s[:,:,1::2, 1::2]).data)
        tensor(0.)

        """
        mask = self.build_mask(x)
        x_masked = mask * x

        if self.scale_translate_net:
            if y is None:
                log_s, t = self.scale_translate_net(x_masked)
            else:
                log_s, t = self.scale_translate_net(x_masked, y)
        else:
            if y is None:
                log_s = self.scale_net(x_masked)
                t = self.translate_net(x_masked)
            else:
                log_s = self.scale_net(x_masked, y)
                t = self.translate_net(x_masked, y)

        log_s = log_s * (1 - mask)
        t = t * (1 - mask)

        return log_s, t

    def forward(self, x, y=None, compute_jacobian=True):
        log_s, t = self.get_parameters(x, y)
        x = x * torch.exp(log_s) + t
        if compute_jacobian:
            self._logdet_jacobian = log_s.view(log_s.size(0), -1).sum(-1)

        return x

    def inverse(self, z, y=None):
        log_s, t = self.get_parameters(z, y)
        z = (z - t) * torch.exp(-log_s)

        return z

    def extra_repr(self):
        return 'in_features={}, mask_type={}, inverse_mask={}'.format(
            self.in_features, self.mask_type, self.inverse_mask
        )


def checkerboard_mask(height, width, inverse_mask=False):
    """
    Parameters
    ----------
    height : int
    width : int
    inverse_mask : bool

    Returns
    -------
    mask : np.array

    Examples
    --------
    >>> checkerboard_mask(5, 4, False)
    array([[1., 0., 1., 0.],
           [0., 1., 0., 1.],
           [1., 0., 1., 0.],
           [0., 1., 0., 1.],
           [1., 0., 1., 0.]], dtype=float32)
    >>> checkerboard_mask(5, 4, True)
    array([[0., 1., 0., 1.],
           [1., 0., 1., 0.],
           [0., 1., 0., 1.],
           [1., 0., 1., 0.],
           [0., 1., 0., 1.]], dtype=float32)

    """
    mask = np.arange(height).reshape(-1, 1) + np.arange(width)
    mask = np.mod((inverse_mask is False) + mask, 2)

    return mask.astype(np.float32)


def channel_wise_mask(channels, inverse_mask=False):
    """
    Parameters
    ----------
    channels : int
    inverse_mask : bool

    Returns
    -------
    mask : np.array

    Examples
    --------
    >>> channel_wise_mask(6, False)
    array([1., 1., 1., 0., 0., 0.], dtype=float32)
    >>> channel_wise_mask(6, True)
    array([0., 0., 0., 1., 1., 1.], dtype=float32)

    """
    mask = np.zeros(channels).astype(np.float32)
    if inverse_mask:
        mask[channels // 2:] = 1
    else:
        mask[:channels // 2] = 1

    return mask
