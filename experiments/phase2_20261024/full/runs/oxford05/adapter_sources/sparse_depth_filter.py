"""Missing-data compatibility for OVO's Gaussian depth-edge filter.

Same kernel, sigma and rejection threshold as the author. Normalize by valid
support instead of treating unknown pixels as zero-depth observations. Invalid
pixels stay zero; rejected observed pixels become -1, as in the author filter.
No depth is created or interpolated at holes. Dense positive input is identical.
"""
import torch
from torchvision.transforms.v2.functional import gaussian_blur


def valid_mask_depth_filter(depth, k_size=7, sigma=2.5, th=.05):
    valid = torch.isfinite(depth) & (depth > 0)
    observed = torch.where(valid, depth, 0)
    numerator = gaussian_blur(observed[None], k_size, sigma)[0]
    denominator = gaussian_blur(valid[None].to(depth.dtype), k_size, sigma)[0]
    low = numerator/denominator.clamp_min(torch.finfo(depth.dtype).eps)
    return torch.where(valid, torch.where((depth-low).abs() > th, -1, depth), 0)


def valid_mask_pooling(candidate, observed, k_size=3):
    """Suppress new points near actual map matches, excluding unobserved holes.

    Author pooling erodes ~candidate, which includes missing depth in RGB-D
    input. On sparse scans that deletes virtually every new point. Only
    observed pixels removed by map matching are evidence of an existing point.
    """
    matched = observed & ~candidate
    near_matched = torch.nn.functional.max_pool2d(
        matched[None].float(), kernel_size=k_size, stride=1, padding=k_size//2)[0].bool()
    return candidate & ~near_matched
