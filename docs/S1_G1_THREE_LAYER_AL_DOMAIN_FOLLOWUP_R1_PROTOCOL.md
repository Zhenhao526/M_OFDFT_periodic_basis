# S1/G1 Al-domain 三层补充验证协议（R1）

协议版本：`S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R1`
实现提交：`7e487ba837636eefca87391cd40a7cba42046134`
正式新 ID：`S1-20260810-319..326`。每个 ID 只允许一次正式尝试；任何失败均停止本 revision，禁止删除、覆盖或重试。

## 1. 范围和前置条件

本轮只补齐既有 Al PBE/3e、同引擎 KS-NL→KS-L 范围中的两个缺口：四个小应变点，以及 `V/V0=0.90/1.10` 两端的独立 k 点和 cutoff 复查。它不改变 PseudoDojo NLPP 与 local BLPS 构造不完全受控的解释边界，也不关闭独立第二 KS/QE 范围，不产生 G4 力/应力验收结论。

正式 runner 启动前，必须在只读父 state `/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_r1_20260810` 中确认 301、302、303、307、308 均有一次 `accepted` 结果，父 session 的 runner commit 精确为 `439bf90049551f2b54bf6a17e0fb8689f5dfb100`。未满足时 fail closed，且不创建本轮 attempt marker。

## 2. 固定计算矩阵

所有点使用 PseudoDojo `Al_std.upf`（PBE/3e，SHA-256 `b02eaa07c5d98f5eeae1bec4155854a7a94ea3635ca6b42e914f3f7ebd6912fb`）、有限温 Fermi-Dirac quarter smearing `0.001837465 Ry`、fresh-start PW-KSDFT、`cal_force=1`、`cal_stress=1` 和 17 位 density cube。

| ID | 几何/角色 | cutoff (Ry) | k 点 |
|---|---|---:|---:|
| 319 | 与 204 的 Al tetragonal `+0.005` geometry payload 逐字节相同 | 40/160 | 28³ |
| 320 | 与 205 的 Al tetragonal `-0.005` geometry payload 逐字节相同 | 40/160 | 28³ |
| 321 | 与 206 的 Al xy shear `+0.005` geometry payload 逐字节相同 | 40/160 | 28³ |
| 322 | 与 207 的 Al xy shear `-0.005` geometry payload 逐字节相同 | 40/160 | 28³ |
| 323 | 与 307 的 `V/V0=0.90` STRU 逐字节相同；normal/extra-k | 40/160 | 32³ |
| 324 | 与 307 的 `V/V0=0.90` STRU 逐字节相同；high/extra-k | 52/208 | 32³ |
| 325 | 与 308 的 `V/V0=1.10` STRU 逐字节相同；normal/extra-k | 40/160 | 32³ |
| 326 | 与 308 的 `V/V0=1.10` STRU 逐字节相同；high/extra-k | 52/208 | 32³ |

“geometry payload”定义为 STRU 从 `LATTICE_CONSTANT` 标题起至 EOF 的原始字节。319–322 只允许把 204–207 的 `ATOMIC_SPECIES` 行从 local PP 改为冻结的 NLPP；payload 必须逐字节相同，并独立重建晶格、体积、Direct 坐标和冻结变形矩阵。323–326 的完整 STRU 必须分别与 307/308 逐字节相同。

## 3. 单点硬门

每个新点必须同时通过：

- solver return code 为 0，且仅有一个收敛标记；
- PP SHA/header 为冻结的 NC/PBE/3e，6 个 radial beta、展开非局域 projector 总数精确为 18；
- `zval*nat = 3 = log Ne = cube integral = eig_occ sum`，cube/eig_occ 相对误差均严格 `<1e-10`；
- `NBANDS=12`，eig_occ 每个 k 点恰有 12 行，最后一带最大占据严格 `<1e-8`；
- cube 原点为零，`n_i*axis_step_i` 与 STRU 三个晶格行逐分量绝对误差 `<5e-5 bohr`，Al1 原子顺序/坐标一致；
- final force block 恰有有序的 Al1 一行；final stress 为对称 3×3，且 `trace(stress)/3` 与 `#TOTAL-PRESSURE#` 绝对差 `<1e-6 kbar`；
- 有限温标签满足 `m=-TS<=0`、`F<=E_ec<=U`、`F=E_KohnSham`、`E_ec=F-m/2`；
- node01、4 MPI ranks 分别 ordered bind 到 physical cores 40–43；正式前执行 live hostname/core/现有 ABACUS affinity preflight；
- attempt marker 用 `O_CREAT|O_EXCL` 在 solver 前落盘；既有 marker/run 目录即禁止同 ID 重试。

## 4. Galileo 科学门

四个 strain 点分别用 301/043 作为 NL/local 的 `V100` 锚：

`delta = [(E_NL,strain-E_NL,301) - (E_L,strain-E_L,043)] * 1000`。

319–322 每点均要求 `abs(delta) <= 20 meV/atom`。local strain 能量固定来自已验收 displacement analysis-R2 的 204–207；043 使用有限温 `E_ec`。

端点能量门必须消除不同 k/cutoff 的绝对能量常数：

- k 门：`abs([(E_extra,end-E_302) - (E_common,end-E_301)]*1000) < 2 meV/atom`；
- cutoff 门：`abs([(E_high,end-E_303) - (E_extra,end-E_302)]*1000) < 1 meV/atom`；
- 压力门：同一端点 `abs(P_high-P_extra) < 0.02 GPa`。

`end` 的 common 点分别为 307/308，extra/high 点分别为 323/324 和 325/326。0.90 与 1.10 两端必须各自三门全过。

## 5. 执行、证据与接收

正式 external state 唯一固定为 `/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_al_domain_followup_r1_20260810`，不得与任何 R1–R4、DFTpy、再生成、位移或三层 R1 state 共用。正式运行使用 node01 physical cores 40–43，与当前三层 R1 的 30–33 隔离。

runner、analyzer 和 validator 必须来自晚于本实现的独立预注册提交。最终分母精确为 8/8，失败/缺失/跳过/重试均为 0；四个 strain gate 和两个端点的六个收敛 gate 全部通过后，才可标为 `accepted_al_domain_followup`。分析证据提交后再以 `--require-committed` 确定性重放。当前实现/预注册阶段不得启动正式 solver。
