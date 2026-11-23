# Sparse Temporal Adapters (STA)

## Problem
Adapting a pretrained video transformer to a new action recognition task usually means
fine-tuning every parameter, which is expensive in memory and storage.

## Core idea
Keep the backbone frozen and insert a small *sparse temporal adapter* after every block.
For frame tokens $x_t$ the adapter computes

    h_t = x_t + W_u * sigma(W_d * sum_{s in N(t)} x_s)

where N(t) is a sparse set of temporal neighbours (the previous frame, the next frame and
one long-range frame). Only W_d and W_u are trained.

## Contributions
1. A parameter-efficient adapter that models temporal interactions only.
2. 1.2M trainable parameters instead of 86M for full fine-tuning.
3. Better top-1 accuracy than full fine-tuning and bottleneck adapters.
