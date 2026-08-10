# S1/G1 Al-domain 三层补充验证协议（R3）

协议版本：`S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R3`
实现提交：`R3_IMPLEMENTATION_COMMIT`
正式新 ID：`S1-20260810-343..350`。每个 ID 只允许一次正式尝试；任何失败均停止本 revision，禁止删除、覆盖或重试。

## 1. R1/R2 关闭与 R3 前置源

R1 follow-up 的预注册 `0785b61c3a414b3c1f31865d31c60093fee849a9` 不改写。其父 R1 在 306 accepted 后、写 P0 phase marker 前连接中断，307--318 永不执行，因此 319--326 以 `superseded_before_execution` 关闭：follow-up state 从未创建，attempt/run/solver 均为 0，八个 ID 永久不得重用。

R2 的首个 prereg `3a3685b2acd3cccbe5e7d1a9f2130d299084e307` 经独立证据链审计拒绝，已由 `86b90318e5cb56464b0eaab1fa993de1cb1c09e5` 关闭为 `superseded_before_execution`。replacement R2 prereg/runner `e57fdacb6cbdc32b20fc29fcb6351a392b49f847` 在 335 的唯一正式 attempt 中由 rank-wrapper fail closed：OpenMPI 选中了 OS CPU 40--43，但冻结实现错误地把这些 OS CPU label 当作 `/sys .../core_id`；node01 上它们实际属于 package 1/core_id 2--5。四 rank 均在写 affinity evidence、`execve` ABACUS 之前退出，故 0 ABACUS、0 accepted，336--342 为 zero attempt。提交 `118b8300c9e2757df91cf2fb4a810663b42aeac4` 已冻结 operational failure；R2 state 和 335--342 永久不可删除、重启、重试或复用。

R3 使用经只读查重确认未占用的新 ID 343--350、新 state 和新 smoke root；这不是 R2 retry。R3 runner 必须先逐字节/逐 SHA 重验上述 R2 closure 与冻结 state snapshot。

R3 runner 必须先 fail-closed 读取两个只读科学源：

1. 原 R1 的 301--306。accepted marker 必须以 `result_sha256` 绑定 result，result 的每个 evidence SHA/size、runner return、session SHA/runner commit 均须重验；continuation R2 的独立 P0 recovery barrier 必须声明原 R1 operational closure 为 `incomplete_missing_phase_marker`、301--306 no-retry/no-reuse，并给出逐 ID inventory 与独立 scientific P0 状态。
2. continuation R2 的 prereg `2c25900d02761f61fb9fe99a3086350f8ea6ab0a`、recovery formalization `3205d4972366c8a53a01fee2c5de22a4028b01eb` 与 barrier SHA `60765edd6b72fbdbe2ac190581b3c7aad1e91e6ef7d7616be1b6d24e29abf353` 必须构成祖先链且外部/versioned barrier 字节一致。其 327（Al v090 normal/common）和 328（Al v110 normal/common）必须各自 marker/result/evidence 完整；`al_eos` 六点 phase closure 必须 accepted，精确列出 327--332，并以逐 ID `accepted_result_sha256` map、session/config/manifest/barrier SHA、detached proof 以及 phase/per-run core-collision preflight SHA 闭合。follow-up parser 从 raw log/cube/eig-occ/force/stress/affinity 独立重放 327--332 全六点；科学端点只取 327/328。只见 `status=accepted` 不足以放行，也不等待其后的 Mg aggregate terminal。

任一文件、SHA、ID、protocol、runner、barrier 或 phase closure 不一致时，在创建本轮 state/attempt 前停止。

## 2. 固定计算矩阵

全部点使用 PseudoDojo `Al_std.upf`（PBE/3e）、40/160 或 52/208 Ry、Fermi-Dirac `sigma=0.001837465 Ry`、fresh-start PW-KSDFT、force/stress、17 位 density cube。

正式新 KS-NL runtime 固定为旧 CPU binary SHA `2d68a57c…`；R4 parent 的 legacy relocated runtime 是 `438c74b9…`。两者不是字节同一实现，只能引用提交 `a01ac707e8e4d2604ea01a947d9c32738aa264df` 的既有 6/6 storage-exact bridge 作为同冻结源的科学等价闭包，禁止写成 runtime identity 完全相同。

| ID | 几何/角色 | cutoff (Ry) | k 点 |
|---|---|---:|---:|
| 343 | Al tetragonal `+0.005`，绑定 204 | 40/160 | 28³ |
| 344 | Al tetragonal `-0.005`，绑定 205 | 40/160 | 28³ |
| 345 | Al xy shear `+0.005`，绑定 206 | 40/160 | 28³ |
| 346 | Al xy shear `-0.005`，绑定 207 | 40/160 | 28³ |
| 347 | v090 normal/extra-k；common=327 | 40/160 | 32³ |
| 348 | v090 high/extra-k；common=327 | 52/208 | 32³ |
| 349 | v110 normal/extra-k；common=328 | 40/160 | 32³ |
| 350 | v110 high/extra-k；common=328 | 52/208 | 32³ |

343--346 不从 204--207 metadata 或 payload 复制构造。generator 必须从 043 基胞独立重建：以晶格行为矩阵 `A`，严格采用 `A'=A F^T`，Direct 坐标字节不变；tetragonal 的 `F=diag(1+s,(1+s)^(-1/2),(1+s)^(-1/2))`，shear 的 `F=I+s e_x⊗e_y`。独立 hard gate 重算 `det(F)=1`、实际矩阵/符号/幅度和 Direct，并再要求所得 geometry payload 与 204--207 分别逐字节一致。347--350 的完整 STRU 与 preregistered v090/v110 geometry 一致，正式时还须与 accepted 327/328 的实际 STRU 逐字节一致。

## 3. 单点和运行硬门

每点必须同时通过：solver return 0/唯一收敛；PP SHA/header PBE/3e、6 个 beta、展开 projector=18；`zval*nat=3=log Ne=cube=eig_occ`（相对误差严格 `<1e-10`）；`NBANDS=12`、last occupation `<1e-8`；cube 原点三个分量显式精确为 0，三个完整有符号轴均逐分量满足 `n_i*step_i=A_i`、误差 `<5e-5 bohr`，Al1 顺序/坐标正确；force 恰一行且有序，stress 最终 3×3 对称、trace/3 与 pressure 差 `<1e-6 kbar`；有限温标签自洽；4 rank ordered binding 必须逐层满足下述 topology contract。analyzer 必须从 raw 重新运行 parser 并与 stored result 字节一致。

核隔离不能把 `pe-list` label、OS logical CPU 与 `/sys core_id` 混写。冻结 host=`node01`、package=`0`；rank 0--3 的 primary OS logical CPU 为 `30,31,32,33`，对应 `/sys core_id=30,31,32,33`，完整 affinity/thread-sibling 行分别为 `[30,106] [31,107] [32,108] [33,109]`，完整 lock/collision domain 为 `30,31,32,33,106,107,108,109`。runner/ACK/rank evidence 必须分别保存并交叉验证 hostname、OS affinity、physical_package_id、core_id 与 siblings。

implementation 后先单独 prereg；随后必须在正式 state/attempt 仍不存在时，以同一 MPI、`pe-list=30,31,32,33:ordered` 和同一 R3 rank-wrapper 做一次真实 detached 4-rank smoke，但 `abacus_exec_count=0`。smoke driver 必须是 setsid session leader、无 TTY、忽略 SIGHUP，并持有同一 8-CPU nonblocking lock；四 rank 的 `sched_getaffinity`、package、core_id、siblings 全部精确通过后才生成外部 O_EXCL aggregate。aggregate 作为 prereg 的唯一 evidence-only child commit 进入版本库；formal runner 必须验证 external/versioned/committed bytes、rank raw SHA、command、detached/lock/preflight、4/4/failed0/RC0/ABACUS0 后，才允许创建 state/session。缺 smoke、smoke 失败、额外 commit path 或任一 identity 不一致均禁止正式 attempt。

正式命令还必须提供新 revision 外部 core-reservation ACK；runner 在 session 前及每个 case attempt 前重复 live topology/collision scan，并全程持锁。无 ACK、任一 lock/collision/detached/smoke/rank topology 不符均 fail closed。

每个新点的 attempt、accepted marker、runner return、metadata、result orchestration identity 与 session 必须共同绑定 experiment ID、protocol、runner commit、config SHA 和 manifest SHA；accepted marker 必须绑定 result SHA。terminal 必须给出精确 8-ID attempted/accepted 分母与 count，并分别冻结 attempt marker、accepted marker、runner return、result、metadata SHA maps、`accepted_result_sha256` map 与 session SHA，禁止仅凭 return code 或 status 接收。

state 固定为 `/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_al_domain_followup_r3_20260810`。attempt 用 `O_CREAT|O_EXCL` 在 solver 前写入；既有 state/marker/run 即禁止重试。

## 4. Galileo 门与科学解释

343--346 分别使用 301/043 锚定：

`delta=[(E_NL,strain-E_NL,301)-(E_L,strain-E_L,043)]*1000`，每点 `abs(delta)<=20 meV/atom`。

端点分别使用 327/328 为 normal/common，并以 301/302/303 消去不同 k/cutoff 的绝对常数：锚定 k 差严格 `<2 meV/atom`，锚定 cutoff 差严格 `<1 meV/atom`，同端点 high-extra 的 `|delta P|<0.02 GPa`。

这是同一引擎下 NLPP 与 local BLPS 的 **scheme plus construction discrepancy/suitability bound**；它不是对孤立 LPP scheme bias 的上界。结果不关闭第二独立 KS/QE，不关闭 G4 力/应力，不采用跨 PP 绝对总能门，也不声称严格零温。

## 5. 接收

最终分母精确 8/8，失败/缺失/跳过/重试为 0；四个 strain 门和两个端点各三门全过；parent/recovery/phase、core ACK/locks/affinity、PP/Ne/cube/force/stress/thermodynamic 证据完整。R1 committed snapshot 复制 stored evidence 与 recovery `enhanced_raw_gates.evidence_files` 的并集并逐 SHA/size 重验；然后对 301--306（含 Mg）逐点重跑冻结 R1 base parser 和 recovery enhanced parser，分别与 stored result 字节及 barrier `enhanced_raw_gates` canonical bytes 完全一致。327--332 与 343--350 同样从 committed raw 独立重放。UPF body 因仓库再分发政策不进入 Git：必须以 config 的 repository/commit/URL/git-blob/SHA/header、run 内 `pseudo_identity.json`、metadata runtime identity、result identity、log projector 总数多重绑定，并由服务器 external cache 做 full SHA/header validation 后在临时 hard-link replay workspace 注入；cache 缺失或任一身份不符即失败，不能静默跳过 PP 门。

terminal accepted 且 runner return 0 后才收集分析，提交 evidence 后以 `--require-committed` 确定性重放。当前只预注册，不创建 follow-up state 或启动正式 solver；前置条件是完整 327--332 `al_eos` phase accepted，而非仅 327/328 单点出现。
