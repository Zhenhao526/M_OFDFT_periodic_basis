# S1/G1 KS-NL → KS-L → OF-L 三层 EOS 验证协议（R1）

协议版本：`S1-G1-THREE-LAYER-20260810-R1`
基线提交：`f10008dd7a6e05cb8bf23f1ffd10e99e19b6083d`
正式新 ID：`S1-20260810-301..318`；任何 ID 至多尝试一次，失败后不得删除、覆盖或重试。

## 1. 本轮能回答什么

本轮使用冻结的 ABACUS `v3.11.0-beta.5` 在同一引擎内做 KS-NL pilot，并与仓库中已冻结的 KS-L、OF-L EOS 比较。Al 主线保持 PBE、3 价电子，形成可量化的七点 KS-NL EOS 硬门；Mg 的新 PP 是 PBE、10 价电子，而旧 KS-L/OF-L 是 LDA-PZ、2 价电子，因此 Mg 只做 ABACUS 兼容性、截断/k 点收敛与三点（资源允许时七点）诊断，绝不进入 Al 硬门分母，也不得表述成 Mg 的 LPP 因果比较。

这是对 D-026 范围的显式修订：D-026 原建议用 Quantum ESPRESSO 作为第二 KS 引擎；R1 改为 ABACUS engine-controlled P0/P1 pilot，以先测量 PBE/3e Al 的 LPP 方案偏差上界。即使所有门都通过，本轮也**不能**声称“第二个独立 KS 引擎”或原 D-026 的 QE 范围已经闭合。PseudoDojo NLPP 与现有 local BLPS 并非同一生成构造，故 KS-NL→KS-L 差值仍混有 PP 构造差异，科学解释仅限“当前 LPP 方案偏差上界”，不是只开关 nonlocal projector 的严格因果实验。

本轮只关闭/拒绝“三层七点 EOS 的 Al 子范围”。相/小应变的 ≤20 meV/atom 门、0.90/1.10 端点的独立 cutoff/k 复查和独立第二 KS 引擎仍由后续冻结协议处理；不得用本轮七点 EOS 代替这些证据。

## 2. 冻结软件与 PP

- ABACUS 二进制：`/home/shenwei01/wt_melting_runtime_20260724/build-abacus-wt-cpu/source/abacus_pw_para`，SHA-256 `2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba`。
- 源码版本：`v3.11.0-beta.5`；对应官方 tag `v3.11.0-beta5`、commit `ee6ec4cdc8956d2f14d84d01a05b8f7e733dfad0`。
- PP 官方来源固定为 PseudoDojo `ONCVPSP-PBE-SR` commit `823b18f7701b6303e0e69114eaa645173f706600`。PP 只下载到仓库外精确 SHA 缓存；不把来源许可仍需澄清的字节并入仓库。
- Al：`Al_std.upf`，UPF 2.0.1、NC/PBE、zval=3、NLCC=true、6 个 radial beta channels（角动量 0,0,1,1,2,2；ABACUS 展开后每原子 `nh=18`）；SHA-256 `b02eaa07c5d98f5eeae1bec4155854a7a94ea3635ca6b42e914f3f7ebd6912fb`。
- Mg：`Mg_std.upf`，UPF 2.0.1、NC/PBE、zval=10、NLCC=false、4 个 radial beta channels（角动量 0,0,1,1；ABACUS 展开后每原子 `nh=8`）；SHA-256 `15cdd9380687a47a1f4bc4ab5fe1f0c6d9c50891350fd8534c8f292b3ce0be14`。
- STRU 明确写 `upf201`，INPUT 明确写 `basis_type pw`、`dft_functional PBE`、`vnl_in_h 1`。`ecutrho=4*ecutwfc`，单位均为 Ry。

PseudoDojo 官方 `standard.djson` 的 low/normal/high hint 单位为 Hartree：Al 16/20/26 Ha，即 32/40/52 Ry；Mg 38/42/48 Ha，即 76/84/96 Ry。本轮 normal/high 直接取 40/52 Ry（Al）和 84/96 Ry（Mg），不得沿用 local PP 的 cutoff 证据。

## 3. 新计算矩阵与阶段锁

机器可读分母位于 `config/S1_g1_three_layer_r1_manifest.tsv`。

P0 恰为 6 个 `V/V0=1.00` 点：

| 材料 | normal/common | normal/extra-k | high/extra-k |
|---|---:|---:|---:|
| Al | 301: 40/160 Ry, 28³ | 302: 40/160 Ry, 32³ | 303: 52/208 Ry, 32³ |
| Mg | 304: 84/336 Ry, 24×24×16 | 305: 84/336 Ry, 28×28×18 | 306: 96/384 Ry, 28×28×18 |

只有 P0 analyzer 写出 `status=accepted` 的外部 barrier 后，才可执行后续 ID。Al `307..312` 补齐 0.90、1.10、0.94、0.97、1.03、1.06，统一使用已过 P0 的 normal/common 设置，并与 301 合成七点。Mg `313..314` 补 0.90/1.10，与 304 合成强制三点诊断；`315..318` 是不影响 R1 验收状态的可选四点。

正式运行限 node01 的物理 core 30–33、4 MPI ranks、每 rank 1 thread。Open MPI 使用 `--map-by pe-list=30,31,32,33:ordered --bind-to core --report-bindings`；逐 rank 记录 hostname、logical CPU affinity、socket/core ID，要求四个 rank 分别落在 core 30、31、32、33。每点从原子密度/波函数 fresh start；不得读取旧 run/state。

外部一次性 state 固定为 `/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_r1_20260810`。该路径不得与 R1–R4、DFTpy 或任何旧 ID 共用。

## 4. P0 fail-closed 门

每个点都必须满足：进程返回 0、日志仅有 `#SCF IS CONVERGED#`、F/m/U/E_ec/压力有限、`m=-TS<=0`、`F<=E_ec<=U`、`E_ec=F-m/2` 文本精度一致；独立重积分 `chg.cube` 的电子数相对误差严格 `<1e-10`；PP 字节/header/zval/projector 数及逐 rank 亲和性与冻结值一致。NL 分支不使用 `T_sU=E_one_elec-E_localpp`，也不声称 local-only 能量分解成立。

逐材料 P0 还要求：

- high/extra-k 相对 normal/extra-k：`|ΔE_ec| < 1 meV/atom` 且 `|ΔP| < 0.02 GPa`；
- normal/extra-k 相对 normal/common：`|ΔE_ec| < 2 meV/atom`。

任一门失败即保留失败 ID 和全部原始证据，R1 立即停止；不得在 R1 内加 cutoff、放宽阈值或重试 ID。新网格只能由新协议修订和新 ID 执行。

## 5. Al 三层 EOS 判据

七点顺序固定为 `0.90,0.94,0.97,1.00,1.03,1.06,1.10`，能量一律按各曲线自身 `V/V0=1.00` 锚定；绝对总能只报告，不跨 PP 直接比较。三条曲线是：

- KS-NL：本轮 Al 307/309/310/301/311/312/308；
- KS-L：已冻结 R4 quarter-smearing 047/073/074/043/075/076/049；
- OF-L：已冻结 high-cutoff 071..077。

三条曲线分别做同一 BM3 拟合。KS-L 相对 KS-NL 的硬门为 `|ΔV0| <=0.5%`、`|ΔB0| <=10%`，并报告七点锚定曲线最大差（仅作 EOS 支持量，不能替代相/应变门）。OF-L 相对 KS-L 的门为 `|ΔV0| <=1%`、`|ΔB0| <=10%`；若不通过，允许且必须标为 `accepted_error_portrait`，形成明确 KEDF 误差画像，但不得表述成 KEDF 物理准确性通过。所有 BM3 `V0` 必须位于采样区间，最大拟合残差必须 `<1 meV/atom`。

R1 的 Al EOS 子范围只有在 P0、7/7 KS-NL、BM3 质量和 KS-L↔KS-NL 硬门全部通过时才可标为 accepted。Mg 的任何结果均不改变该状态。

## 6. 证据与最终接收

正式 solver 必须晚于包含本协议、config、manifest、generator、NL parser、runner、analyzer、validator、rank wrapper、测试及生成输入的预注册提交。每个 attempt marker 用 `O_CREAT|O_EXCL` 先于 solver 创建；任何已有 marker 都表示同 ID 不可再执行。

分析收集每点 `INPUT/STRU/KPT`、PP SHA/header（不收集 PP 本体）、`running_scf.log`、`chg.cube`、stdout/stderr、逐 rank affinity、result 与 attempt marker，并记录源参考文件 SHA/commit。最终分析与证据必须提交；随后 validator 以 `--require-committed` 在干净树上独立重放并返回 0。通过后仍不合并、不推送，等待主任务审阅。
