def running_max(nums):
    """Running maximum at each position."""
    out = []
    m = nums[0]
    for n in nums:
        if n > m:
            m = n
        out.append(m)
    return out
