# S1/G1 Al-domain 三层补充验证协议（R2）

协议版本：`S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R2`
实现提交：`0000000000000000000000000000000000000000`
正式新 ID：`S1-20260810-335..342`。每个 ID 只允许一次正式尝试；任何失败均停止本 revision，禁止删除、覆盖或重试。

## 1. R1 关闭与 R2 前置源

R1 follow-up 的预注册 `0785b61c3a414b3c1f31865d31c60093fee849a9` 不改写。其父 R1 在 306 accepted 后、写 P0 phase marker 前连接中断，307--318 永不执行，因此 319--326 以 `superseded_before_execution` 关闭：follow-up state 从未创建，attempt/run/solver 均为 0，八个 ID 永久不得重用。

R2 runner 必须先 fail-closed 读取两个只读源：

1. 原 R1 的 301--306。accepted marker 必须以 `result_sha256` 绑定 result，result 的每个 evidence SHA/size、runner return、session SHA/runner commit 均须重验；continuation R2 的独立 P0 recovery barrier 必须声明原 R1 operational closure 为 `incomplete_missing_phase_marker`、301--306 no-retry/no-reuse，并给出逐 ID inventory 与独立 scientific P0 状态。
2. continuation R2 的 327（Al v090 normal/common）和 328（Al v110 normal/common）。两点必须各自 marker/result/evidence 完整、属于冻结 continuation prereg commit，且 endpoint phase closure 已 accepted 并绑定 session。只见 `status=accepted` 不足以放行。

任一文件、SHA、ID、protocol、runner、barrier 或 phase closure 不一致时，在创建本轮 state/attempt 前停止。

## 2. 固定计算矩阵

全部点使用 PseudoDojo `Al_std.upf`（PBE/3e）、40/160 或 52/208 Ry、Fermi-Dirac `sigma=0.001837465 Ry`、fresh-start PW-KSDFT、force/stress、17 位 density cube。

| ID | 几何/角色 | cutoff (Ry) | k 点 |
|---|---|---:|---:|
| 335 | Al tetragonal `+0.005`，绑定 204 | 40/160 | 28³ |
| 336 | Al tetragonal `-0.005`，绑定 205 | 40/160 | 28³ |
| 337 | Al xy shear `+0.005`，绑定 206 | 40/160 | 28³ |
| 338 | Al xy shear `-0.005`，绑定 207 | 40/160 | 28³ |
| 339 | v090 normal/extra-k；common=327 | 40/160 | 32³ |
| 340 | v090 high/extra-k；common=327 | 52/208 | 32³ |
| 341 | v110 normal/extra-k；common=328 | 40/160 | 32³ |
| 342 | v110 high/extra-k；common=328 | 52/208 | 32³ |

335--338 不从 204--207 metadata 或 payload 复制构造。generator 必须从 043 基胞独立重建：以晶格行为矩阵 `A`，严格采用 `A'=A F^T`，Direct 坐标字节不变；tetragonal 的 `F=diag(1+s,(1+s)^(-1/2),(1+s)^(-1/2))`，shear 的 `F=I+s e_x⊗e_y`。独立 hard gate 重算 `det(F)=1`、实际矩阵/符号/幅度和 Direct，并再要求所得 geometry payload 与 204--207 分别逐字节一致。339--342 的完整 STRU 与 preregistered v090/v110 geometry 一致，正式时还须与 accepted 327/328 的实际 STRU 逐字节一致。

## 3. 单点和运行硬门

每点必须同时通过：solver return 0/唯一收敛；PP SHA/header PBE/3e、6 个 beta、展开 projector=18；`zval*nat=3=log Ne=cube=eig_occ`（相对误差严格 `<1e-10`）；`NBANDS=12`、last occupation `<1e-8`；cube 原点三个分量显式精确为 0，`n_i*step_i=A_i` 误差 `<5e-5 bohr`、Al1 顺序/坐标正确；force 恰一行且有序，stress 最终 3×3 对称、trace/3 与 pressure 差 `<1e-6 kbar`；有限温标签自洽；4 rank ordered bind physical cores 40--43。

核隔离不是一次 `comm` 扫描即可通过。正式命令必须提供外部 core-reservation ACK；runner 对 40--43 全程持有 nonblocking exclusive advisory locks，再执行 hostname/online/affinity/ABACUS collision preflight；rank wrapper 对每 rank 实际 affinity fail closed 并留证。无 ACK、任一 lock 失败、collision 或 rank affinity 不符均不得继续。

state 固定为 `/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_al_domain_followup_r2_20260810`。attempt 用 `O_CREAT|O_EXCL` 在 solver 前写入；既有 state/marker/run 即禁止重试。

## 4. Galileo 门与科学解释

335--338 分别使用 301/043 锚定：

`delta=[(E_NL,strain-E_NL,301)-(E_L,strain-E_L,043)]*1000`，每点 `abs(delta)<=20 meV/atom`。

端点分别使用 327/328 为 normal/common，并以 301/302/303 消去不同 k/cutoff 的绝对常数：锚定 k 差严格 `<2 meV/atom`，锚定 cutoff 差严格 `<1 meV/atom`，同端点 high-extra 的 `|delta P|<0.02 GPa`。

这是同一引擎下 NLPP 与 local BLPS 的 **scheme plus construction discrepancy/suitability bound**；它不是对孤立 LPP scheme bias 的上界。结果不关闭第二独立 KS/QE，不关闭 G4 力/应力，不采用跨 PP 绝对总能门，也不声称严格零温。

## 5. 接收

最终分母精确 8/8，失败/缺失/跳过/重试为 0；四个 strain 门和两个端点各三门全过；parent/recovery/phase、core ACK/locks/affinity、PP/Ne/cube/force/stress/thermodynamic 证据完整。terminal accepted 且 runner return 0 后才收集分析，提交 evidence 后以 `--require-committed` 确定性重放。当前任务只实现和预注册，不启动正式 solver，等待 327/328。
