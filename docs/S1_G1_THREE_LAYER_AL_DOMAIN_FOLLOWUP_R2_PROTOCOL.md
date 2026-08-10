# S1/G1 Al-domain 三层补充验证协议（R2）

协议版本：`S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R2`
实现提交：`64cd874e43e2fb14399349a9826f6b47700f9443`
正式新 ID：`S1-20260810-335..342`。每个 ID 只允许一次正式尝试；任何失败均停止本 revision，禁止删除、覆盖或重试。

## 1. R1 关闭与 R2 前置源

R1 follow-up 的预注册 `0785b61c3a414b3c1f31865d31c60093fee849a9` 不改写。其父 R1 在 306 accepted 后、写 P0 phase marker 前连接中断，307--318 永不执行，因此 319--326 以 `superseded_before_execution` 关闭：follow-up state 从未创建，attempt/run/solver 均为 0，八个 ID 永久不得重用。

R2 runner 必须先 fail-closed 读取两个只读源：

1. 原 R1 的 301--306。accepted marker 必须以 `result_sha256` 绑定 result，result 的每个 evidence SHA/size、runner return、session SHA/runner commit 均须重验；continuation R2 的独立 P0 recovery barrier 必须声明原 R1 operational closure 为 `incomplete_missing_phase_marker`、301--306 no-retry/no-reuse，并给出逐 ID inventory 与独立 scientific P0 状态。
2. continuation R2 的 prereg `2c25900d02761f61fb9fe99a3086350f8ea6ab0a`、recovery formalization `3205d4972366c8a53a01fee2c5de22a4028b01eb` 与 barrier SHA `60765edd6b72fbdbe2ac190581b3c7aad1e91e6ef7d7616be1b6d24e29abf353` 必须构成祖先链且外部/versioned barrier 字节一致。其 327（Al v090 normal/common）和 328（Al v110 normal/common）必须各自 marker/result/evidence 完整；`al_eos` 六点 phase closure 必须 accepted，精确列出 327--332，并以逐 ID `accepted_result_sha256` map、session/config/manifest/barrier SHA、detached proof 以及 phase/per-run core-collision preflight SHA 闭合。follow-up parser 从 raw log/cube/eig-occ/force/stress/affinity 独立重放 327--332 全六点；科学端点只取 327/328。只见 `status=accepted` 不足以放行，也不等待其后的 Mg aggregate terminal。

任一文件、SHA、ID、protocol、runner、barrier 或 phase closure 不一致时，在创建本轮 state/attempt 前停止。

## 2. 固定计算矩阵

全部点使用 PseudoDojo `Al_std.upf`（PBE/3e）、40/160 或 52/208 Ry、Fermi-Dirac `sigma=0.001837465 Ry`、fresh-start PW-KSDFT、force/stress、17 位 density cube。

正式新 KS-NL runtime 固定为旧 CPU binary SHA `2d68a57c…`；R4 parent 的 legacy relocated runtime 是 `438c74b9…`。两者不是字节同一实现，只能引用提交 `a01ac707e8e4d2604ea01a947d9c32738aa264df` 的既有 6/6 storage-exact bridge 作为同冻结源的科学等价闭包，禁止写成 runtime identity 完全相同。

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

每点必须同时通过：solver return 0/唯一收敛；PP SHA/header PBE/3e、6 个 beta、展开 projector=18；`zval*nat=3=log Ne=cube=eig_occ`（相对误差严格 `<1e-10`）；`NBANDS=12`、last occupation `<1e-8`；cube 原点三个分量显式精确为 0，三个完整有符号轴均逐分量满足 `n_i*step_i=A_i`、误差 `<5e-5 bohr`，Al1 顺序/坐标正确；force 恰一行且有序，stress 最终 3×3 对称、trace/3 与 pressure 差 `<1e-6 kbar`；有限温标签自洽；4 rank ordered bind physical cores 40--43。analyzer 必须从 raw 重新运行 parser 并与 stored result 字节一致。

核隔离不是一次 `comm` 扫描即可通过。正式命令必须提供外部 core-reservation ACK；冻结物理核为 40--43，完整 logical/SMT 冲突与 lock domain 为 `40,41,42,43,116,117,118,119`。runner 对这 8 个 logical CPU 全程持有 nonblocking exclusive advisory locks，在 session 前及每个 case 的 attempt 前扫描全部 ABACUS 进程和显式绑定的三层 rank wrapper；rank wrapper 对每 rank 实际 affinity fail closed 并留证。正式进程还必须以 `setsid` 成为无 TTY 的 session leader、忽略 SIGHUP，并把 detached proof 写入 session。无 ACK、任一 lock 失败、collision、detached proof 或 rank affinity 不符均不得继续。

每个新点的 attempt、accepted marker、runner return、metadata、result orchestration identity 与 session 必须共同绑定 experiment ID、protocol、runner commit、config SHA 和 manifest SHA；accepted marker 必须绑定 result SHA。terminal 再以精确 8-ID `accepted_result_sha256` map 与 session SHA闭合，禁止仅凭 return code 或 status 接收。

state 固定为 `/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_al_domain_followup_r2_20260810`。attempt 用 `O_CREAT|O_EXCL` 在 solver 前写入；既有 state/marker/run 即禁止重试。

## 4. Galileo 门与科学解释

335--338 分别使用 301/043 锚定：

`delta=[(E_NL,strain-E_NL,301)-(E_L,strain-E_L,043)]*1000`，每点 `abs(delta)<=20 meV/atom`。

端点分别使用 327/328 为 normal/common，并以 301/302/303 消去不同 k/cutoff 的绝对常数：锚定 k 差严格 `<2 meV/atom`，锚定 cutoff 差严格 `<1 meV/atom`，同端点 high-extra 的 `|delta P|<0.02 GPa`。

这是同一引擎下 NLPP 与 local BLPS 的 **scheme plus construction discrepancy/suitability bound**；它不是对孤立 LPP scheme bias 的上界。结果不关闭第二独立 KS/QE，不关闭 G4 力/应力，不采用跨 PP 绝对总能门，也不声称严格零温。

## 5. 接收

最终分母精确 8/8，失败/缺失/跳过/重试为 0；四个 strain 门和两个端点各三门全过；parent/recovery/phase、core ACK/locks/affinity、PP/Ne/cube/force/stress/thermodynamic 证据完整。R1 committed snapshot 复制 stored evidence 与 recovery `enhanced_raw_gates.evidence_files` 的并集并逐 SHA/size 重验；327--332 与 335--342 均从 committed raw 独立重放。UPF body 因仓库再分发政策不进入 Git：必须以 config 的 repository/commit/URL/git-blob/SHA/header、run 内 `pseudo_identity.json`、metadata runtime identity、result identity、log projector 总数多重绑定，并由服务器 external cache 做 full SHA/header validation 后在临时 hard-link replay workspace 注入；cache 缺失或任一身份不符即失败，不能静默跳过 PP 门。

terminal accepted 且 runner return 0 后才收集分析，提交 evidence 后以 `--require-committed` 确定性重放。当前只预注册，不创建 follow-up state 或启动正式 solver；前置条件是完整 327--332 `al_eos` phase accepted，而非仅 327/328 单点出现。
