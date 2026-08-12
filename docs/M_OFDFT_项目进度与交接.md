# M-OFDFT 项目进度与交接

> 本文件是项目状态的唯一人工入口。任何人接手前先读本文件，再读项目书和当前阶段 README。  
> 状态词仅使用：`not_started`、`in_progress`、`blocked`、`accepted`、`rejected`、`paused`。  
> 更新时间：2026-08-12 12:10 CST
> 文档版本：V4.7

## 0. 十分钟上手摘要

| 项目 | 当前值 |
|---|---|
| 当前总状态 | `in_progress`（S1/G1 已验收；S2/G2 架构竞赛已预注册） |
| 当前阶段 | S2：周期原子/混合密度基表示验证 |
| 当前闸门 | G2a/G2b；先验证表示/算子正确性、规范唯一性和几何连续性 |
| 当前负责人 | 远端账户 `shenwei01`；本轮执行与记录：Codex |
| 当前工作分支 | `codex/r4-execution`；所有实现和计算均在远端服务器完成 |
| 最近可用提交 | 规模/几何 R3 实现 `c6202428`；预注册 `da16a237`；证据 `472b6c35`；主线合并 `c001cfde` |
| 最近通过的数值 smoke | `S1-RUNTIME-SMOKE-20260805-074`：`storage_exact`，五类状态门全部 `accepted`；幂等重验返回 `accepted_committed` |
| 最近通过的正式分析 | G1 acceptance policy R1：R3/R5 全量重放、17 行 gate、`accepted_g1_6_of_6`、新增 solver 0；证据 `d0386418` |
| 当前阻塞 | 128³/16 相位网格诊断已将候选新增伪力降至 `6.28e-6 eV/Å`，说明旧伪力超门主要是 96³ 离散化；仍未闭合的科学阻塞是几何路径总秩 `2171–2172/2173` |
| 下一项唯一动作 | 预注册新 revision：固定 128³ 或更密的实空间网格口径，并将满代数秩门改为可证明连续的有效子空间/冗余模态门；不进入 G2c 或 S3 |

### 必读文件

1. [V2.1 项目书](M_OFDFT_周期金属原子密度基组研究计划书.md)
2. [G1 标签审计 R1 协议](S1_G1_THERMODYNAMIC_LABEL_AUDIT_R1_PROTOCOL.md)
3. G1 标签审计 R1 预注册：`config/S1_g1_thermodynamic_label_audit_r1.json`、`config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv`
4. R1 034 失败闭包：`failed_runs/runtime_relocation/S1-20260806-034/attempt-df57f9b610d8/thermodynamic_label_failure_classification.json`、`thermodynamic_label_status.json`、`thermodynamic_label_failure_artifact_inventory.json`
5. [G1 标签审计 R2 协议](S1_G1_THERMODYNAMIC_LABEL_AUDIT_R2_PROTOCOL.md)、`config/S1_g1_thermodynamic_label_audit_r2.json`、`config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv`
6. R2 041 失败闭包：`failed_runs/runtime_relocation/S1-20260806-041/attempt-ff26667f881e/thermodynamic_label_failure_classification_r2.json`、`thermodynamic_label_status_r2.json`、`thermodynamic_label_parser.stderr.txt`
7. [G1 标签审计 R3 协议](S1_G1_THERMODYNAMIC_LABEL_AUDIT_R3_PROTOCOL.md)、terminal `0d984d0` 与只读失败归档
8. [G1 标签审计 R4 协议](S1_G1_THERMODYNAMIC_LABEL_AUDIT_R4_PROTOCOL.md) 与 R4 scripts/tests
9. [G1 电子数 R2 协议](S1_G1_ELECTRON_NUMBER_AUDIT_R2_PROTOCOL.md)
10. G1 电子数 R2 正式分析：`analysis/s1/electron_number_audit_r2_20260805/README.md`、`analysis/s1/electron_number_audit_r2_20260805/summary.json`
11. [扩大文献调研与可行性再评估](M_OFDFT_扩大文献调研与可行性再评估_2026-08.md)
12. [扩展文献逐篇研判](references/M_OFDFT_extended/M_OFDFT_扩展文献逐篇研判.md)
13. [扩展参考文献包索引](references/M_OFDFT_extended/README.md)
14. [初次可行性复核](M_OFDFT_任务书可行性复核.md)
15. [原参考文献精读报告](references/M_OFDFT/M_OFDFT_参考文献逐篇精读报告.md)
16. [原参考文献包索引](references/M_OFDFT/README.md)
17. [G1 DFTpy 跨代码协议](S1_G1_CROSS_CODE_DFTPY_PROTOCOL.md)、`config/S1_g1_cross_code_dftpy.json`
18. DFTpy R2 正式分析：`analysis/s1/g1_cross_code_dftpy_r2_20260810/README.md`、`summary.json`
19. [G1 10 例再生成 R2 协议](S1_G1_REGENERATION_10_R2_PROTOCOL.md)、`analysis/s1/g1_regeneration_10_r2_20260810/README.md`
20. [G1 位移/应变 R1 协议](S1_G1_DISPLACEMENT_STRAIN_REFERENCE_R1_PROTOCOL.md)、`analysis/s1/g1_displacement_strain_reference_r1_20260810/README.md`
21. [G1 位移/应变 analysis-only R2 协议](S1_G1_DISPLACEMENT_STRAIN_REFERENCE_ANALYSIS_R2_PROTOCOL.md)、`analysis/s1/g1_displacement_strain_reference_analysis_r2_20260810/README.md`
22. [G1 Al 1% 验收协议](S1_G1_AL_ACCEPTANCE_POLICY_R1_PROTOCOL.md)、`analysis/s1/g1_al_acceptance_policy_r1_20260811/{README.md,summary.json,gates.tsv,analysis_revision.json}`
23. 三层 R3 历史拒绝：`analysis/s1/g1_three_layer_analysis_r3_20260810/`；R5 应变/端点闭包：`analysis/s1/g1_three_layer_al_domain_followup_analysis_r5_20260810/`
24. [S2 规模/几何 R3 协议](S2_G2_AL_SCALE_GEOMETRY_R3_PROTOCOL.md)、`analysis/s2/g2_al_scale_geometry_r3_20260811/{README.md,summary.json,geometry_metrics.tsv,scale_metrics.tsv}`
25. [S2 局域 108 原子 R1 协议](S2_G2_AL_LOCALIZED_EGGBOX_RANK_R1_PROTOCOL.md) 与 parser failure closure：`analysis/s2/g2_al_localized_eggbox_rank_r1_20260811/operational_failure_closure.json`
26. [S2 局域 108 原子 analysis-only R2 协议](S2_G2_AL_LOCALIZED_ANALYSIS_R2_PROTOCOL.md)、`analysis/s2/g2_al_localized_analysis_r2_20260811/{README.md,summary.json,rank_continuity.tsv,eggbox_metrics.tsv}`
27. S2 96³→128³ 网格加密诊断：`analysis/s2/g2_al_localized_grid_refinement_diag_r1_20260812/{README.md,summary.json,result_96.json,result_128.json}`

### 最近可运行命令

```bash
ssh -p 39987 liangkun@180.184.249.155
ssh -p 2200 shenwei01@localhost
cd /home/shenwei01/M_OFDFT_periodic_basis
./scripts/run_unit_tests.sh
python3 scripts/validate_s1_electron_number_audit_r2.py --require-committed --require-all-runs
python3 scripts/validate_s1_g1_thermodynamic_label_audit_r1.py config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv --config config/S1_g1_thermodynamic_label_audit_r1.json --require-committed --check-failure-archives S1-20260806-034
python3 -s scripts/generate_s1_g1_thermodynamic_label_audit_r4.py --project-root "$PWD"
taskset -c 10 /home/shenwei01/.local/venvs/m_ofdft-dftpy-2.2.0-py311/bin/python scripts/validate_s1_g1_cross_code_dftpy.py analysis/s1/g1_cross_code_dftpy_r2_20260810 --require-committed
/usr/bin/python3 -s /home/shenwei01/M_OFDFT_g1_regen10_r1_20260810/scripts/validate_s1_g1_regeneration_10_r2.py --require-committed
/usr/bin/python3 -s /home/shenwei01/wt_g1_displacement_r1_20260810/scripts/validate_s1_g1_displacement_strain_reference_r2.py --require-committed
cd /home/shenwei01/wt_g1_al_acceptance_policy_r2_20260811 && env PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 python3 -s -B scripts/validate_s1_g1_al_acceptance_policy_r1.py --require-committed
cd /home/shenwei01/wt_s2_g2_al_scale_geometry_r3_final_20260811 && env OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 /home/shenwei01/.local/venvs/m_ofdft-dftpy-2.2.0-py311/bin/python -s -B scripts/validate_s2_g2_al_scale_geometry_r3.py --require-committed
python3 -m unittest -q tests.unit.test_s1_g1_thermodynamic_label_audit_r4_generator tests.unit.test_s1_g1_thermodynamic_label_audit_r4_parser tests.unit.test_s1_g1_thermodynamic_label_audit_r4_validator tests.unit.test_s1_g1_thermodynamic_label_audit_r4_analysis tests.unit.test_s1_g1_thermodynamic_label_audit_r4_runner tests.unit.test_s1_g1_thermodynamic_label_audit_r4_launcher
./scripts/run_smoke.sh S0-YYYYMMDD-NNN
```

## 1. 本次工作记录

### 本次目标

- 将 V1.0 任务书改为分阶段闸门式 V2.0；
- 为每一步定义量化验收、挑战标准、失败处置和交付物；
- 建立本进度与交接文档。
- 登录远端服务器并执行 S0 环境盘点；
- 建立独立远端仓库和 fcc Al/WT smoke test。

### 本次完成

- [x] 完成 7 篇原始文献检索，其中 6 篇全文精读、1 篇摘要级定位；
- [x] 补充并精读 Mi et al. 2023 OFDFT 综述；
- [x] 完成可行性复核；
- [x] 完成 V2.0 阶段与闸门设计；
- [x] 建立进度与交接模板；
- [x] 建立远端项目目录、环境快照、软件清单和自动测试入口；
- [x] 固化 ABACUS v3.11.0-beta.5、OpenMPI 5.0.10、LibXC 7.0.0 及二进制/源码哈希；
- [x] 固化 Al、Mg BLPS 文件及 SHA-256；
- [x] 完成 `S0-20260805-001` 两次 fcc Al/WT 自洽计算；
- [x] 两次计算均收敛，总能均为 -228.73364097028113 eV，重复差 0.0 meV/atom；
- [x] 完成初始 Git 提交及 `s0-smoke-20260805` 标签；
- [x] 在 `env -i` 隔离 shell 中完成 2/2 单测及 `S0-20260805-002` 双重复 smoke，耗时 12.22 秒；
- [x] 以提交 `5c6a63423c3c0cca0ac46002fa1df9212b480a6e` 和标签 `s0-isolated-smoke-20260805` 固化隔离恢复状态；
- [x] 固定单位、能量口径、实验/结构命名和文件格式协议；
- [x] 将 `main` 和全部 S0 标签上传至 `https://github.com/Zhenhao526/M_OFDFT_periodic_basis.git`；
- [x] 生成 443 MB 锁定运行时归档，SHA-256 为 `5fbfa016...dad6bdd`；
- [x] 在全新空目录恢复 1.3 GB 运行时，恢复 11.13 秒；当时的 ABACUS `ldd` 报告有 32 项解析到恢复前缀、0 项解析到旧前缀，但该检查不覆盖 MPI/UCX whole-runtime 调用链；
- [x] 完成 `S0-20260805-003`：恢复加单测/smoke 总计 23.46 秒，重复差 0.0 meV/atom；
- [x] 完成 G0 数值重复性与锁定归档恢复审核并允许进入内部研究阶段 S1；runtime-isolation 子项已在后续审计中重开为 `paused`；
- [x] 建立 S1 候选协议、28 个 EOS 输入、14 个截断扫描输入和 7/7 单元测试；
- [x] 完成 Al WT 20/30/40/60 Ry 扫描，20 Ry 已满足相邻加密阈值；
- [x] 保留 `S1-20260805-004` 未收敛结果，并在提交 S1-R1 后以 `S1-20260805-005` 成功复核；
- [x] 完成 Mg WT 30/40/60/80 Ry 扫描，四点全部收敛，30 Ry 为 V0 最小通过候选；
- [x] 完成 Al/Mg KSDFT 40/60/80 Ry 截断扫描，二者均选择 40 Ry；
- [x] 完成并扩展 Al k 点扫描至 24³；热力学口径复核后 20³→24³ 为 2.024876 meV/atom，原 20³ 结论已撤回并新增 28³ 候选；
- [x] 完成 `S1-20260805-028` Al 28³ 确认，24³→28³ 的零温外推能量变化 0.822250 meV/atom，最终选择 24³；
- [x] 冻结 S1-R7 EOS 候选参数并生成 14 个唯一结构、42 次计算、6 条七点曲线的双展宽矩阵及固定实验 ID 清单；
- [x] 保留 S1-029 后定位 MPI 吞食清单 stdin 问题，以独立文件描述符修复并增加消耗 stdin 的回归测试；
- [x] 完成 S1-029 至 070：42/42 收敛、0 失败、6/6 BM3 曲线通过；Al/Mg 双展宽最大相对能差为 0.135259/0.205258 meV/atom，Veq 差为 0.027655%/0.031817%；
- [x] 完成独立只读审计：42 个原始日志与解析一致，168/168 归档输入校验和通过，六曲线拟合独立复现；
- [x] 完成并扩展 Mg k 点扫描至 24x24x16，尾部稳定判据选择 20x20x12；
- [x] 完成 Al/Mg 标准与半展宽 V0 诊断，四点全部收敛；明确单点绝对能量位移不能替代 EOS 相对能量验收；
- [x] 单元测试扩展至 25/25，通过输入、解析、清单 stdin 隔离、BM3 拟合、端点诊断、OF/KS 基准与双展宽严格门槛检查；
- [x] 冻结 S1-R8：六条七点加密曲线、42 个新计算、S1-071 至 112 固定 ID；不重跑 28 个 S1-R7 唯一基线点；
- [x] 新增 R8 内容寻址配置、输入/manifest 预检、恢复安全运行器和原始七点严格验收器；34/34 单测及空结果负路径通过；
- [x] 完成 S1-071 至 112：42/42 数值收敛、6/6 加密曲线严格验收，原始结果终点为 `300a2aa`；
- [x] 以 `9010eed` 固化分析溯源与严格验收硬化，以 `d28126b` 固化 `analysis/s1/non_equilibrium_convergence_20260805/` 正式分析；
- [x] 后续 runtime 审计确认原 G0/S1 证据只证明 ABACUS `ldd` 解析，不证明 whole-runtime/hermetic：恢复前缀 `mpirun` 可转调旧前缀 `prterun`，运行探针也出现旧前缀成功访问；
- [x] 将 runtime-relocation/namespace 协议硬化至 92/92 单元测试通过，并完整保留三次失败 smoke 档案；
- [x] 完成受管 074 smoke：提交 `92e513f`，66 个证据文件，能量/压力逐存储位一致，五类状态门全部 `accepted`；
- [x] 以 `9a0fd7d` 冻结 S1-113–118 config/manifest，SHA-256 分别为 `9130989f...a0a4a7`、`6cdaa3e6...6195c1`；
- [x] 完成 S1-113–118 六点串行复演：六点逐点 committed validator 通过，结果提交终点 `ce51927`；
- [x] 以 `a01ac70` 固化正式分析：6/6 科学门、6/6 runtime 审计、6/6 R8 替换结论通过，六点均为 `storage_exact`；
- [x] 以 `f4a816a` 实现 G1 电子数增量 R2，以 `125dd37` 硬化归档尝试顺序验证；全套单元测试 123/123 通过；
- [x] 以 `b18106b` 预注册 S1-130–148 的 19 点 R2 continuation，完成 19/19 新执行点且 R2 新失败为 0，结果终点为 `c722c81`；
- [x] 以 `c94796d` 固化正式电子数分析：90/90 accepted，覆盖 84 个 primary baseline 与 6 个 supplemental runtime replay；证据拆分为 60 KS、11 R1 reused OF 和 19 R2 executed OF；
- [x] 电子数审计最大 `certified_relative_error` 为 `1.0127696865884852e-11`（源点 `S1-20260805-044`），30/30 OF 科学等价、120/120 KMP rank lifecycles、360/360 成功 syscall 均通过，四类 failure ID 列表全空；
- [x] 以 `64ce08e` 实现并以 `f71dd6b` 预注册 G1 第三 smearing/稠密 k 标签审计 R1；协议、输入与 40 个固定 ID 均在首个正式运行前冻结；
- [x] R1 P0 4/4 通过，P1 前 6/8 通过；共 10 个新运行逐点 accepted，执行顺序为 `024,036,031,039,021,035,027,037,028,038`，结果终点 `9096ca3`；
- [x] `S1-20260806-034` 的 ABACUS 与 namespace 内部 runtime audit 完成，但 SSH/PTY 宿主编排在后置核验前中断，缺失 `host_status.json`、`counterpart_audit.json` 与 `result.json`；冻结分类为 `indeterminate`、`workflow_or_runtime_capability_failure`、`runtime_kmp`；
- [x] 以 `df57f9b` 固化 034 的 71 文件失败闭包，以紧邻提交 `b0b7db5` 将同一 tree 移至 `failed_runs/runtime_relocation/S1-20260806-034/attempt-df57f9b610d8/`；committed archive validator 通过，R1 停止且 034 永久禁止同 ID 重跑；
- [x] 将 R1 accepted/失败/归档证据经校验 bundle 同步至本地并推送 GitHub `main`；本地 `audit.json`、`objects.tsv`、`tmp/` 保持未跟踪且未修改；
- [x] 完成标签审计 R2 执行前六轮复核与硬化：sealed memfd `200/201/202`、全重放链科学输入贯通、GO/marker 因果门、terminal/journal exact type 以及 finalize 稳定字节/HEAD 复验均已封闭；本地 206/206、远端 Linux 206/206 单测通过，第六轮无 P0/P1；
- [x] 以 `d73e2ba` 冻结 R2 执行实现，以 `329a200` 单独预注册 30 个新 ID/120 个输入，config/manifest SHA-256 分别为 `c38ffa3f...ab658`、`3c5ff41c...af1`；
- [x] 以 `99deacd` 固化脱离 SSH/PTY 的 supervisor 证明，GO 绑定 sealed runner/config/manifest 后启动；首个 marker `314ac53` 在 solver 前通过因果 barrier；
- [x] R2 041 的 ABACUS 在 22:24.42 内收敛，`run_status=accepted`、runtime audit `accepted`、电子数 4.0/4.0；但 R2 解析器因 `configuration execution order differs` 返回 1，core validator 返回 97；
- [x] 监督器将结果以 `ff26667` 记录，再以紧邻提交 `f91a300` 原样归档到 `failed_runs/runtime_relocation/S1-20260806-041/attempt-ff26667f881e/`；failure-archive barrier 通过，terminal `stopped`/97，042–070 未执行，无 completion；
- [x] 再次只读复验 R1 的 10 个历史 accepted 候选：`021/035/037` 仍通过，另 7 点因当前 `/etc/ld.so.cache` SHA-256 `5050081a...1861f` 与历史 `f55b9dd8...cce50` 不同而 fail closed；未见科学阈值越界；
- [x] 决定 R3 不选择性复用 3 个残余可重放点、不放宽 runtime 门：R1 historical reused/contribution 固定为 0，全部 40 个逻辑槽位使用 `S1-20260807-001`–`040` 在当前冻结环境重算；
- [x] 以 `e31f456` 冻结 R3 协议、generator、生产 parser、validator、runner、launcher、analyzer 和六组测试；P1 为 001–012 的六个全新 common/extra 锚点对，k gate 后才释放 013–040；
- [x] R3 本地 75/75 测试通过，另 4 项 Linux `/proc`/memfd 集成测试按平台预期跳过；三路独立冻结复核均为 P0=0、P1=0，dry-run 为 0 reused + 40 new、40 行 manifest、160 个 input blob；
- [x] R3 随后正式预注册并执行：001 在 1300.99 秒内 accepted；002 因 SIGSTOP 状态检查竞争在 SCF 前以 runtime exit 98 停止，失败 `56f2dcb`、归档 `20be191`、terminal barrier `0d984d0`；003–040 未运行，R3 不重试、不 finalize；
- [x] 复核定位两项根因：基础 launcher 错把 `State:` 字段名中的小写 t 当作 stopped，旧失败归档器又无条件要求只在 payload=0 后产生的 counterpart；未见 smearing/k 数值阈值失败；
- [x] 新建 R4 全新 041–080：独立 shim 在 map capture 前后精确确认 T/t 并记录 rank proof；pre-counterpart 例外仅接受 run/audit/host/payload 四层一致的非零早停；R3 十提交停止链以只读 bridge 绑定且贡献 0；
- [x] R4 远端 Linux 完整套件 90/90，通过真实 `/proc` SIGSTOP 集成；dry-run 为 40 行 manifest、160 个输入 blob，P1=041–052，P2=053–080；
- [x] R4 正式完成：40/40 全新运行、42/42 EOS 标量点、6/6 EOS、14/14 half-quarter 场对、6/6 k-anchor 对、160/160 rank lifecycles 与 480/480 syscalls 全部 `accepted`；failure ledger 全空；
- [x] R4 terminal 为 `accepted`、runner return code 为 0，completion `c4e98320` 已提交并完成最终 committed replay；G1 从 1/6 更新为 2/6；
- [x] 在仓库外冻结 DFTpy 2.2.0 + pylibxc/LibXC 7.0.0 + Python 3.11 环境和依赖哈希；Al/Mg V100 诊断 pilot 均先通过能量、压力和电子数复核；
- [x] DFTpy R1 的 14 个 ID 完成计算，但因逐点 stdout 未被 DFTpy 全局输出对象接管、独立积分使用了不一致的 Bohr 常数而判为证据 `rejected`；R1 state 原样冻结、ID 不重试，科学数值不提升为验收证据；
- [x] 以 `4643b65` 冻结 DFTpy R2 的日志捕获和网格体积修正，以全新 `DFTPY-S1-20260810-015`–`028` 执行 14 个七点 EOS；14/14 accepted、14/14 日志含收敛标记，结果提交 `f10008d`；
- [x] DFTpy R2 正式跨代码门通过：Al/Mg 锚定相对 EOS RMS 为 `1.1279e-05/4.1272e-04 meV/atom`，V0 差为 `3.64e-07/8.75e-05%`，最大压力差为 `0.001036/0.000105 GPa`，最大独立电子数误差为 `3.55e-15/5.77e-15`；G1 从 2/6 更新为 3/6；
- [x] 10 例单命令再生成 R1 在 004 的 4 秒短任务上因 `/proc/cmdline` 瞬时为空触发 runtime 轮询假阴性；R1 仅 001–003 accepted，随后 `failed_no_retry`，全部 R1 state/ID 冻结且对验收贡献 0；
- [x] 以 `ea90c3e` 预注册再生成 R2 的全新 011–020 和逐 rank O_EXCL proof/ACK barrier；10/10 accepted、失败/缺失/跳过/重试均为 0，9 项硬门全通过，能量、压力、72 标签、密度和势差最大值均为 0，证据提交 `d57216d`；G1 从 3/6 更新为 4/6；
- [x] 位移/应变 R1 以 201–215 完成 15/15 solver、零失败/重试、累计 4650.7945 秒；旧 analyzer 对 203 合法 PBC wrap 作直接 Cartesian 相减而假失败，R1 analysis 明确 `rejected_analysis_false_negative`，solver state 不改、不重算；
- [x] 位移/应变 analysis-only R2 复用同一 15 点并加入 minimum-image 与独立 STRU/F 重建：15/15、7/7、300/300 SHA/size 全通过；最大位移误差 `4.4034e-12 Å`、F 误差 `2.22e-16`、cube 几何误差 `1.9819e-05 bohr`、电子数相对误差 `8.3786e-12`、热力学恒等式残差 `1.5261e-10 eV/atom`；证据 `28cd249`，G1 从 4/6 更新为 5/6；
- [x] 完成 Al 三层 R3：旧预注册 `|ΔV0|≤0.5%` 下 `0.7660177%` 被如实判为 `evidence_valid_scientific_gate_rejected`；R5 复用 351–358 的 8/8 结果确认应变最大 `0.238321 meV/atom`，端点 k/cutoff/压力门全部通过；
- [x] 根据 2026-08-11 用户授权，以 analysis-only policy R1 将 Al `|ΔV0|` 门修订为 `≤1%`，完整重放 R3/R5 且不启动 solver；17/17 行门通过，committed validator 返回 `accepted_g1_6_of_6`，证据 `d0386418`；G1 从 5/6 更新为 6/6；
- [x] 限定最终范围为 Al ABACUS 同引擎、登记 PP 对的 scheme+construction suitability bound；Mg 仅 diagnostic，OF-L↔KS-L 保持 error portrait，第二 KS/QE、projector-only 因果和 G4 力/应力均未关闭；HQLPP/QE binding smoke 失败冻结、禁止重试、贡献 0；
- [x] 固定 23 函数 `r08_eta100_complementary`，完成 7 个原胞几何与 32/108 原子共 14 个周期平铺规模案例；7/7、14/14 accepted，committed validator 与 8/8 回归通过，新增 solver 0；
- [x] 32/108 原子冻结低 G 集分别为 194/654 个半空间整数向量，总基函数数为 645/2173；电子数、逐原子算子广延性和系数规模门全部通过；
- [x] 扩大调研至原包 8 篇核心文献、扩展包 13 篇全文/241 页、1 篇网页全文及 20 余篇方法/软件补充证据；
- [x] 识别 AMD-OFDFT 2014 直接先例，收窄“原子中心密度 + 变分 + Pulay 力”的创新主张；
- [x] 完成闸门式项目再评估：整体 66/100、S0–S4A 核心 73/100、全范围 S0–S7 约 43/100；
- [x] 将项目书更新为 V2.1：新增三层赝势/KEDF 验证、展宽标签审计、S2 架构竞赛、低 G/egg-box/性能闸门和独立 S4C；
- [x] 建立扩展文献索引、逐篇研判和综合再评估报告；
- [ ] 选择项目对外发布许可证；

### 下次开始位置

从最终 G1 证据 `d0386418` 及其主线合并 `da15ac0` 开始；全部历史 external state 只读，不删除、不重启、不重试历史 ID：

1. G1 六个子项已 6/6 accepted；复验必须使用各登记 source worktree 的 committed validator，不得改写历史 R3 0.5% rejection；
2. S2/G2 周期平铺规模/几何 pilot 已 accepted；下一动作是以新 revision 建立局域扰动 108 原子独立参考并验证 eggbox/秩连续性；
3. QE/第二 KS、Mg 硬比较、HQLPP 与 projector-only 因果若继续，必须使用新协议；它们属于外推/诊断限制，不得改写 G1 历史；
4. S2 前保留所有 G1 state、失败 smoke 和 analysis evidence 为只读，不再消费 G1 ID。

## 2. 阶段总览

| 阶段 | 名称 | 状态 | 开始日期 | 结束日期 | 闸门 | 证据链接 | 下一动作 |
|---|---|---|---|---|---|---|---|
| S0 | 初始化与复现协议 | `accepted` | 2026-08-05 | 2026-08-05 | G0 | `docs/G0_ACCEPTANCE.md`; `analysis/s1/runtime_relocation_equivalence_20260805/` | 数值/归档恢复结论保留；登记的 namespace runtime-isolation 路径已验收，原归档本身不称 hermetic |
| S1 | 平面波基准闭环 | `accepted` | 2026-08-05 | 2026-08-11 | G1 | 前五项证据同前；三层 R3 `73b3589`；R5 `5401943`；1% policy `d0386418` | G1 6/6；进入 S2 |
| S2 | 混合密度基表示 | `in_progress` | 2026-08-11 | — | G2 | 架构 `cdf90874`；Al1 pilot `269fe3a9`；收敛 R2 `b6985a16`；规模/几何 R3 `472b6c35` | 周期平铺规模门已过；下一步局域 108 原子参考与 eggbox/秩连续性 |
| S3 | 固定 KEDF 自洽求解 | `not_started` | — | — | G3 | — | 等待 G2 |
| S4A | 固定晶胞解析力 | `not_started` | — | — | G4A | — | 等待 G3 |
| S4B | 晶胞应力 | `not_started` | — | — | G4B | — | 等待 G4A |
| S4C | 短时 NVE | `not_started` | — | — | G4C | — | 等待 G4A；建议等待 G4B |
| S5 | ML-KEDF | `not_started` | — | — | G5 | — | 等待 G3，建议等待 G4A |
| S6 | 合金、缺陷、动力学 | `not_started` | — | — | G6 | — | 等待 G4A；ML 分支另需 G5 |
| S7 | 性能、发布与论文 | `not_started` | — | — | G7 | — | 持续准备，最终验收 |

## 3. 已验收阶段：S0

### S0 验收清单

- [x] 确定代码仓库绝对路径和远端地址：`/home/shenwei01/M_OFDFT_periodic_basis`、`https://github.com/Zhenhao526/M_OFDFT_periodic_basis.git`；
- [ ] 写入许可证与 README（README 已完成；许可证待负责人决定，作为对外发布限制移交 S7）；
- [x] 固定主基准与 KSDFT 程序版本：ABACUS v3.11.0-beta.5；
- [x] 建立运行时包清单、CMake 缓存和系统快照；
- [x] 收集 Al、Mg LPP 并记录来源和 SHA-256；
- [x] 建立单位、能量口径和命名规范：`docs/S0_REPRODUCIBILITY_PROTOCOL.md`；
- [x] 建立 fcc Al smoke test；
- [x] 在全新空目录恢复锁定二进制环境并完成测试，共 23.46 秒，满足 <60 分钟；
- [x] 重复运行能量差 0.0 meV/atom，满足 <0.1 meV/atom；
- [x] 建立自动测试入口，2/2 单元测试通过；
- [x] 填写 G0 决策记录：`docs/G0_ACCEPTANCE.md`。
- [x] 以受管 074 与 S1-113–118 whole-runtime 追踪证明登记 namespace 路径中旧前缀成功访问/执行/映射均为 0、未知失败探针为 0；原 ABACUS `ldd` 仍不单独构成该证明。

### 当前阶段文件

| 类型 | 路径/链接 | 状态 | 说明 |
|---|---|---|---|
| 任务书 | [V2.1 项目书](M_OFDFT_周期金属原子密度基组研究计划书.md) | 已完成 | V2.1 |
| 扩大可行性再评估 | [综合报告](M_OFDFT_扩大文献调研与可行性再评估_2026-08.md) | 已完成 | 66/100；核心 S0–S4A 为 73/100 |
| 初次可行性复核 | [初次报告](M_OFDFT_任务书可行性复核.md) | 已完成 | 初次立项依据，保留用于审计演变 |
| 参考文献证据 | [原包索引](references/M_OFDFT/README.md)；[扩展包索引](references/M_OFDFT_extended/README.md) | 已完成 | 原创研判与索引入库；PDF/全文抽取保留在受控本地包，不公开再分发 |
| G1 电子数 R2 协议 | `docs/S1_G1_ELECTRON_NUMBER_AUDIT_R2_PROTOCOL.md` | 已执行 | 复用 R1 accepted 11 点并新执行 R2 19 点；只关闭电子数子项 |
| G1 电子数 R2 分析 | `analysis/s1/electron_number_audit_r2_20260805/` | `accepted` | 90/90；分析提交 `c94796d`；与 R4 合计 G1 总体 2/6 |
| G1 标签审计 R1 协议 | `docs/S1_G1_THERMODYNAMIC_LABEL_AUDIT_R1_PROTOCOL.md` | 已执行并停止 | 实现 `64ce08e`；预注册 `f71dd6b`；R1 ID 不得重跑 |
| G1 标签审计 R1 证据 | `runs/S1-20260806-021/` 等 10 个 accepted 目录；`failed_runs/runtime_relocation/S1-20260806-034/attempt-df57f9b610d8/` | `indeterminate_paused` | 10 accepted；034 capability failure `df57f9b`→相邻归档 `b0b7db5`；无最终 analysis，G1 仍 1/6 |
| G1 标签审计 R2 协议 | `docs/S1_G1_THERMODYNAMIC_LABEL_AUDIT_R2_PROTOCOL.md`；`config/S1_g1_thermodynamic_label_audit_r2.json`；`config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv` | 已执行并停止 | 实现 `d73e2ba`；预注册 `329a200`；脱离证明 `99deacd`；R2 ID/state 不得复用 |
| G1 标签审计 R2 失败证据 | `failed_runs/runtime_relocation/S1-20260806-041/attempt-ff26667f881e/` | `rejected` | 041 solver/runtime accepted，parser registration rejected；`ff26667`→相邻归档 `f91a300`；042–070 未执行，无 analysis/completion，G1 仍 1/6 |
| G1 标签审计 R3 证据 | R3 protocol/config/manifest；`runs/S1-20260807-001/`；002 failure archive；R3 orchestration | 已执行并停止 | 001 accepted；002 runtime race；terminal `0d984d0`；003–040 未运行；R3 state/ID 不得改动 |
| G1 标签审计 R4 完成证据 | `analysis/s1/g1_thermodynamic_label_audit_r4_20260807/`；`orchestration/s1/g1_thermodynamic_label_audit_r4_20260807/supervisor_completion.json` | `accepted` | 40/40 新运行；42/42 scalar；14/14 smearing pairs；6/6 k pairs；completion `c4e98320` |
| G1 10 例再生成 R2 | `docs/S1_G1_REGENERATION_10_R2_PROTOCOL.md`；`analysis/s1/g1_regeneration_10_r2_20260810/` | `accepted` | R1 runtime 假阴性冻结；R2 10/10、零失败/重试；证据 `d57216d` |
| G1 位移/应变 analysis-only R2 | `docs/S1_G1_DISPLACEMENT_STRAIN_REFERENCE_ANALYSIS_R2_PROTOCOL.md`；`analysis/s1/g1_displacement_strain_reference_analysis_r2_20260810/` | `accepted` | R1 solver 15/15；R2 修复 PBC 分析假阴性，15/15、7/7、300/300；证据 `28cd249` |
| G1 三层 R3 历史分析 | `analysis/s1/g1_three_layer_analysis_r3_20260810/` | `rejected`（证据有效） | 旧 0.5% 门下 `|ΔV0|=0.7660177%`；`|ΔB0|=2.46585%` 与 BM3 门通过；历史结论不回写 |
| G1 三层 R5 应变/端点 | `analysis/s1/g1_three_layer_al_domain_followup_analysis_r5_20260810/` | `accepted` | 351–358 为 8/8；应变最大 `0.238321 meV/atom`；端点 k/cutoff/压力均通过；新增 solver 0 |
| G1 1% policy R1 | `docs/S1_G1_AL_ACCEPTANCE_POLICY_R1_PROTOCOL.md`；`analysis/s1/g1_al_acceptance_policy_r1_20260811/` | `accepted` | R3/R5 committed replay；17 行 gate；`accepted_g1_6_of_6`；证据 `d0386418` |
| 代码仓库 | `https://github.com/Zhenhao526/M_OFDFT_periodic_basis` | 已建立 | 服务器路径 `/home/shenwei01/M_OFDFT_periodic_basis`；node01 通过校验 bundle 中转，由本地推送并以 `ls-remote` 核验 |
| 环境锁 | `/home/shenwei01/M_OFDFT_periodic_basis/environment/` | 已建立 | 包清单、CMake、系统快照 |
| 软件清单 | `/home/shenwei01/M_OFDFT_periodic_basis/manifests/` | 已建立 | 二进制、源码包与 LPP 哈希 |
| 复现协议 | `/home/shenwei01/M_OFDFT_periodic_basis/docs/S0_REPRODUCIBILITY_PROTOCOL.md` | 已完成 | 固定单位、能量口径、ID、随机种子和格式 |
| 运行时归档 | `/home/shenwei01/M_OFDFT_runtime_20260805.tar.gz` | 已通过 | 443 MB；SHA-256 `5fbfa016...dad6bdd` |
| G0 验收 | `/home/shenwei01/M_OFDFT_periodic_basis/docs/G0_ACCEPTANCE.md` | 已复核 | 数值/归档恢复保留；登记的重定位+namespace 路径隔离 `accepted`，原 S0 归档本身不称 hermetic |
| smoke test | `/home/shenwei01/M_OFDFT_periodic_basis/runs/S0-20260805-003/` | 数值通过 | 新恢复前缀；单测 2/2；双重复耗时 12.33 秒；不再作为 whole-runtime 隔离证明 |

## 3A. 已验收阶段：S1

- 状态：`accepted`；G1 六个子项 6/6 已完成，S1 于 2026-08-11 关闭。
- 核心材料：fcc Al、hcp Mg；每种至少七个体积点 `0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10 V0`。
- 当前工作仅限基准协议、输入生成、收敛扫描、EOS 与交叉核验；不得提前进入 S2 或 ML。
- 当前结果：Al WT 在 V0 的 20→30 Ry 变化为 0.011269 meV/atom、0.0000990 GPa；20 Ry 为最小通过候选。
- 当前结果：Mg WT 30→40 Ry 变化为 0.00000651 meV/atom、0.0000070 GPa；30 Ry 为 V0 最小通过候选。
- 当前结果：Al/Mg KS 截断均选择 40 Ry；Al k 网格选择 24³，Mg 选择 20x20x12。
- 当前结果：42/42 核心 EOS 收敛且六曲线验收通过；Al/Mg 展宽减半的最大相对能差为 0.135259/0.205258 meV/atom，Veq 差为 0.027655%/0.031817%。
- 当前结果：OFDFT 相对标准展宽 KSDFT 的最大锚定曲线差为 Al 13.064922、Mg 4.819330 meV/atom，为基准诊断，无 G1 通过阈值。
- 当前结果：S1-R8 在 `d6ffe59` 预注册；S1-071 至 112 已 42/42 收敛，六组比较 6/6 `accepted`；原始结果终点 `300a2aa`、硬化 `9010eed`、正式分析 `d28126b`。
- 六组正式指标（最大相对能量差 / 最大压力差 / BM3 最大残差，单位 meV/atom / GPa / meV/atom）：Al OF cutoff `0.001538733 / 0.0001703 / 0.017076344`；Al KS cutoff `0.006794400 / 0.0071189 / 0.009295506`；Al KS kmesh `0.392503700 / 0.0307428（诊断） / 0.008623252`；Mg OF cutoff `0.000007179 / 0.0000431 / 0.002466982`；Mg KS cutoff `0.000705450 / 0.0001021 / 0.003863243`；Mg KS kmesh `0.403149550 / 0.0171684（诊断） / 0.004079008`。
- 当前结果：074 受管 smoke、正式预注册和 S1-113–118 六点复演均已完成；正式分析 `a01ac70` 为 6/6 `storage_exact`、6/6 runtime accepted、6/6 R8 替换结论不变。
- 当前结果：G1 电子数 R2 正式分析 `c94796d` 为 90/90 `accepted`；84 个 primary + 6 个 supplemental，60 KS + 11 R1 reused OF + 19 R2 executed OF，最大认证相对误差 `1.0127696865884852e-11`，30/30 OF 科学等价及 120/120、360/360 KMP 门全部通过，R2 新失败为 0。
- 当前结果：G1 标签审计 R1 已接受 10 个新运行（P0 4/4、P1 前 6/8）；P0 字段门通过，Al/Mg V100 common→extra-quarter 的绝对 Eec 差分别为 1.0504/0.9370 meV/atom；Mg 0.90 V0 锚点差为 0.8496 meV/atom。
- 当前结果：R1 034 因宿主后置证据闭包缺失为 `indeterminate_paused`，不是数值 rejection；失败 `df57f9b` 与归档 `b0b7db5` 相邻且 tree 一致，040/P2 未执行，R1 不产生最终 analysis。
- 当前结果：R2 完成预注册和脱离监督，041 的 SCF 收敛，`F=-24.5932301833`、`E_ec=-24.5928592294`、`U=-24.5924882754 eV/atom`，报告/期望电子数 4.0/4.0（归档 cube 独立只读积分相对误差约 `4.85e-13`），runtime audit `accepted`；这些只是已归档数值事实，不是正式标签验收。
- 当前结果：R2 parser 在读取数值标签前，因冻结 R1 registration validator 写死 `len(execution_order)==40` 而拒绝合法的 30 行 R2 manifest/order；terminal `stopped`/97，041 验收分母贡献 0，042–070 未执行，无 R2 analysis/completion。
- 当前结果：远端只读重放 R1 的 10 个历史 accepted 候选时，`021/035/037` 通过，其余 7 点因 `/etc/ld.so.cache` 当前 SHA-256 `5050081a...1861f` 不同于 R1 记录 `f55b9dd8...cce50` 而 fail closed；未观察到科学数值越阈。
- 当前结果：R3-001 accepted；R3-002 在 SCF 前因 SIGSTOP 状态检查竞争以 runtime exit 98 停止并归档；terminal barrier `0d984d0`，003–040 未执行，R3-001 对 R4 分母贡献 0。
- 当前结果：R4 使用全新 041–080；40/40 runs、42/42 scalar points、6/6 EOS、14/14 half-quarter pairs、6/6 k pairs 全部 `accepted`，R1/R2/R3 贡献为 0。
- 当前验证：160/160 rank lifecycles 与 480/480 runtime syscalls 通过；terminal `accepted`、runner=0、completion `c4e98320`；最终 committed replay validator 通过，failure ledger 全空。
- 科学结论：Al/Mg 相邻 smearing 的最大锚定能量差分别不超过 `0.206652/0.060495 meV/atom`，平衡体积差均小于 `0.013%`；稠密 k 门 6/6 通过。标签为有限温 Mermin 量，`zero_temperature_exact_claim=false`。
- 当前结果：独立 DFTpy 2.2.0 R2 使用全新 015–028 共 14 个七点 Al/Mg EOS；14/14 收敛并 accepted，日志、压力、密度积分和环境身份完整。Al/Mg 锚定 EOS RMS 为 `1.1279e-05/4.1272e-04 meV/atom`，V0 差 `3.64e-07/8.75e-05%`，最大压力差 `0.001036/0.000105 GPa`，均显著通过预注册门。
- 证据处置：DFTpy R1 的 001–014 因空逐点 stdout 与 Bohr 常数证据实现错误而整体 rejected；错误不影响其观测到的科学趋势，但 R1 不计分、不重试。R2 独立积分最大误差为 Al `3.55e-15`、Mg `5.77e-15`，修正后 committed validator 通过。
- 当前结果：10 例再生成 R1 的短任务 runtime 轮询假阴性已冻结并排除；R2 的 011–020 为 10/10 accepted，失败/缺失/跳过/重试为 0，能量、压力、标签、密度和势的 replay 最大差为 0，committed validator 通过。
- 当前结果：位移/应变 R1 求解为 15/15、零失败/重试；旧 analyzer 对 203 周期等价坐标产生假阴性。analysis-only R2 用 minimum-image 和独立 STRU/F 重建复析原证据后 15/15、7/7、300/300 全通过，无需重算 solver；中心差分保持 G1 diagnostic-only，不提前关闭 G4。
- G1 为 `accepted`（6/6）：前五项保持 accepted；第六项在保留旧 0.5% 拒绝历史的同时，按新 1% Al 限定门由 policy R1 完整重放后通过。
- runtime-isolation 复核：原 `ldd` 外推仍撤回；登记的重定位 ABACUS + 私有 namespace + 严格审计路径已通过 074 和六点正式复演，因此该限定子项 `accepted`。锁定归档本身仍不得称为天然 hermetic。
- S1 后续仅作只读基准来源；不得触碰已关闭 G1 子项的 state/ID。

## 3B. 当前阶段：S2

- 状态：`in_progress`；G2 架构竞赛 R1 已完成实现 `f84c31b0` 和预注册 `cdf90874`，主线合并为 `ad402466`。
- R1 登记 12 个架构单元：纯 PW/FFT、原子基+FFT、显式原子+低 G、互补投影/范围分离四路线，分别覆盖 1、32、108 原子。
- 32/108 原子胞由固定整数超胞矩阵定义；完美晶体周期复制只用于超胞等价性和性能诊断，不冒充 108 原子局域扰动参考。
- 当前仅启用 Al、G2a/G2b；Mg、ML 和系数空间自洽优化保持关闭。G2c 只有在表示正确性和连续性通过后才执行。
- 参考密度绑定 G1 已提交的 Al KS-NL V100 cube；全部 G1 state/ID 保持只读。
- R1 验证结果：12/12 case、13/13 生成物、7/7 单测、预注册 validator `accepted`；新增 solver 0，formal state 与 analysis root 均不存在。
- Al 单原子 analysis-only pilot 已以实现 `022551e4`、预注册 `8f00abf3`、证据 `269fe3a9` 闭合；committed validator 与 7/7 单测通过，新增 solver 0。
- 纯原子六高斯路线密度 L2 为 `6.6669%`，出现负密度，Hartree/外势/XC 合计误差 `74.4553 meV/atom`，固定 WT 误差 `527.7920 meV/atom`，首层淘汰。
- 显式低 G 与互补路线的密度 L2 均为 `0.577099%`，Hartree/外势/XC 合计误差约 `0.3643 meV/atom`，但固定 WT 误差约 `15.0379 meV/atom`，超过 10 meV/atom 门，因此均为 `rejected_pilot`。
- 互补化在不改变拟合空间/精度的情况下，将归一化重叠条件数由 `101280` 降至 `6494`，确认其规范稳定性优势；这不等于已经通过 G2a。
- 收敛 R2 以实现 `5eb3ae49`、预注册 `9e561bf4`、证据 `b6985a16` 闭合：4/6/8/10 个径向函数 × 6 个低 G 壳层 × 2 种规范，共 48 个压缩候选；24/24 显式/互补对通过等价性门，25 个候选、其中 15 个互补候选通过全部原门。
- 最小合格互补候选为 `r08_eta100_complementary`：8 个径向函数 + 14 个实低 G 函数 + 1 个常数，共 23 个函数；密度 L2 `0.562997%`、最小密度 `0.00221312 e/bohr^3`、条件数 `204761`、三算子合计误差 `0.270272 meV/atom`、固定 WT 误差 `4.003408 meV/atom`。
- 同空间显式规范的条件数是互补规范的 `35.5894` 倍，进一步支持把互补规范作为后续架构候选；本结论只关闭 Al 单原子表示/算子收敛子步骤。
- 规模/几何 R3 以实现 `c6202428`、预注册 `da16a237`、证据 `472b6c35` 闭合：固定同一 23 函数候选，原胞平衡态加六个 ±0.5% 各向同性/四方/剪切变形为 7/7 accepted；32/108 原子周期平铺为 14/14 accepted，新增 solver 0。
- 七个原胞案例的密度 L2 P95 为 `0.573791%`，固定 WT 误差 P95 为 `4.035124 meV/atom`；有效秩恒为 23，最大条件数 `220049.327`，电子数相对误差均约 `3.2e-13`。
- 32/108 原子各自冻结平衡态整数低 G 集（194/654 个半空间向量），全部几何保持同一函数列；基函数总数为 645/2173，系数占网格点比例约 `0.1458%/0.1455%`，逐原子算子差最大仅浮点舍入量级。
- 本结论只接受完美周期平铺的几何连续性与规模稳定性；阈值重选跨界被保留为诊断，不允许改变冻结基组。局域缺陷 108 原子参考、eggbox/伪力、完整大胞 Gram 与 G2 overall 均未关闭。
- 局域 108 原子 R1 以 `S2-20260811-001` 建立真实 KS-NL 独立参考：34 次电子迭代收敛、324 电子、96³ 电荷网格、16-rank 亲和性完整；R1 随后因旧单原子 cube parser 假阴性停止，solver 不重跑。
- Analysis-only R2 严格重放 R1 的 38 个 state 文件（33,441,717 B）后完成科学分析。密度 L2 `0.467935%`、三算子合计误差 `1.188731 meV/atom`、固定 WT 总误差 `5.741805 meV/atom`、eggbox 能量峰峰值 `0.374359 meV/atom` 均通过。
- 固定 23 函数候选仍被科学拒绝：五点总秩为 `2171–2172`，低于登记的 2173；最大 eggbox 伪力为 `0.008360315 eV/Å`，超过 `0.002 eV/Å`。G2 overall 保持未通过。
- 2026-08-12 进行了不新增 solver 的网格加密诊断：同一参考/投影密度在 96³ 的 16 相位对照复现参考/候选/新增伪力 `0.002893/0.009044/0.006152 eV/Å`；周期傅里叶重采样到 128³ 后降至 `7.18e-6/2.61e-6/6.28e-6 eV/Å`，电子数保持到 `1.14e-13 e`。这支持“旧伪力失败由 96³ 分析网格混叠主导”，但本轮为事后诊断，对正式验收贡献为 0。
- 当前唯一动作：新 revision 前瞻性冻结 128³+网格，并用近零模态/主角连续性重写秩门；通过前不进入 G2c 或 S3。

## 4. 闸门决策记录

每个闸门只允许一个当前结论：`accepted`、`rejected` 或 `paused`。修订结论时新增一行，不覆盖旧记录。

| 日期 | 闸门 | 结论 | 审核人 | 量化结果摘要 | 证据路径 | 后续决定 |
|---|---|---|---|---|---|---|
| 2026-08-05 | G0 | `paused` | 待填写 | 仅完成文档设计，尚无运行环境和 smoke test | 本文件 | 完成 S0 后正式验收 |
| 2026-08-05 | G0 | `paused` | Codex | 远端环境、哈希、单元测试和 smoke 已完成；许可证与干净环境重建未完成 | `S0-20260805-001` | 保持 S0，不进入 S1 |
| 2026-08-05 | G0 | `paused` | Codex | Git 基线已固化；隔离 shell 恢复 12.22 秒并重复差 0.0 meV/atom；从零安装和外部许可决策未完成 | `S0-20260805-002` | 保持 S0，不进入 S1 |
| 2026-08-05 | G0 | `paused` | Codex | GitHub 远端已建立并完成 `main`/标签上传；许可证、LPP 再分发和从零安装仍未完成 | `s0-github-sync-20260805` | 保持 S0，不进入 S1 |
| 2026-08-05 | G0 | `accepted` | Codex | 锁定二进制归档从空目录恢复 11.13 s；恢复加单测/smoke 23.46 s；动态库旧前缀引用 0；重复差 0.0 meV/atom；单测 2/2 | `docs/G0_ACCEPTANCE.md`; `S0-20260805-003` | 允许内部 S1；许可证/LPP 再分发继续限制发布 |
| 2026-08-05 | G0/runtime-isolation | `paused` | Codex | 原“旧前缀引用 0”仅为 ABACUS `ldd` 解析；whole-runtime 追踪发现恢复 `mpirun` 转调旧前缀 `prterun` 且存在旧路径成功访问 | `docs/G0_ACCEPTANCE.md` | 保留归档恢复与数值观察；修复协议→074 smoke→113–118，未通过前撤回 hermetic 主张 |
| 2026-08-05 | G0/runtime-isolation | `accepted` | Codex | 074 与 S1-113–118 在登记的重定位+私有 namespace 路径下全部通过；每点成功旧访问/执行/映射、未知探针均为 0，六点数值逐存储位一致 | `analysis/s1/runtime_relocation_equivalence_20260805/`; `a01ac70` | 接受限定部署路径；保留 S0-003 erratum，不把原归档本身称为天然 hermetic |
| 2026-08-06 | G1/thermodynamic-label R1 | `paused` | Codex | 10 个新运行 accepted；034 的 SCF 与 inner audit 完成，但宿主 `host_status`/counterpart/result 闭包缺失；权威分类 `indeterminate`，71 文件失败证据与相邻归档均通过 validator | `df57f9b`; `b0b7db5`; `failed_runs/runtime_relocation/S1-20260806-034/attempt-df57f9b610d8/` | R1 停止且所有 R1 ID 禁止重跑；G1 保持 1/6；只允许新 revision + 新 IDs continuation |
| 2026-08-06 | G1/thermodynamic-label R2 | `rejected` | Codex | 041 的 SCF、通用结果解析和 runtime/KMP 均 accepted；R2 parser 在数值标签解析前因 R1 registration validator 写死 40 项而拒绝合法 30 项 order；terminal stopped/97，042–070 未执行 | `ff26667`; `f91a300`; `failed_runs/runtime_relocation/S1-20260806-041/attempt-ff26667f881e/` | R2 停止，禁止 finalize/继续/重跑；041 不计 accepted；G1 保持 1/6；只允许新 R3 revision + 新 IDs |
| 2026-08-07 | G1/thermodynamic-label R3 | `rejected` | Codex | 001 accepted；002 因 SIGSTOP 状态检查竞争在 SCF 前 runtime exit 98；失败/归档/barrier 为 `56f2dcb`/`20be191`/`0d984d0`，003–040 未运行 | R3 run001、002 archive、orchestration barrier | R3 停止，不重试、不 finalize、不改 state；全部 R4 分母贡献 0 |
| 2026-08-07 | G1/thermodynamic-label R4 | `in_progress` | Codex | 全新 041–080；R4 stop-confirmed shim 与严格 pre-counterpart 状态机；远端 Linux 90/90；dry-run 40 行/160 blobs | R4 protocol/scripts/tests；分支 `codex/r4-execution` | 实现提交后单独预注册并脱离启动，先执行 P1 041–052；G1 保持 1/6 |
| 2026-08-08 | G1/thermodynamic-label R4 | `accepted` | Codex | 40/40 runs、42/42 scalar、6/6 EOS、14/14 smearing pairs、6/6 k pairs；160/160 rank lifecycles、480/480 syscalls；failure ledger 为空 | `analysis/s1/g1_thermodynamic_label_audit_r4_20260807/`；completion `c4e98320` | 关闭第二个 G1 子项；G1 更新为 2/6；继续剩余四项 |
| 2026-08-10 | G1/cross-code DFTpy R1 | `rejected` | Codex | 14 点科学计算完成，但逐点 stdout 为空且独立积分常数与 DFTpy 网格不一致，证据契约失败 | external R1 state；`analysis_r1` | 冻结 001–014，不重试、不计分；修正实现后启用 R2 新 ID |
| 2026-08-10 | G1/cross-code DFTpy R2 | `accepted` | Codex | 14/14 accepted；Al/Mg 七点相对 EOS RMS `1.13e-5/4.13e-4 meV/atom`，ΔV0、压力、电子数全部通过 | `analysis/s1/g1_cross_code_dftpy_r2_20260810/`；`f10008d` | 关闭第三个 G1 子项；G1 更新为 3/6；继续三层、位移/应变和再生 |
| 2026-08-10 | G1/regeneration-10 R1 | `rejected` | Codex | 001–003 accepted；004 solver/science 通过但 runtime argv 轮询假阴性；005–010 未执行 | external R1 state；failure closure `8560974` | R1 `failed_no_retry`，全部 ID/state 冻结且贡献 0；新 R2/new IDs |
| 2026-08-10 | G1/regeneration-10 R2 | `accepted` | Codex | 10/10；失败/缺失/跳过/重试 0；9 项 hard gates；最大科学 replay 差 0 | `analysis/s1/g1_regeneration_10_r2_20260810/`；`d57216d` | 关闭第四个 G1 子项；G1 更新为 4/6 |
| 2026-08-10 | G1/displacement-strain R1 analysis | `rejected` | Codex | solver 15/15；旧 analyzer 对 203 合法周期 wrap 产生非 PBC Cartesian 假阴性 | external R1 state；`analysis/s1/g1_displacement_strain_reference_r1_20260810/` | solver 不重算；R1 analysis 贡献 0；新 analysis-only R2 |
| 2026-08-10 | G1/displacement-strain analysis R2 | `accepted` | Codex | 15/15、7/7、300/300；独立几何、Ne、Mermin identity、force/stress 与 PBC 门全通过 | `analysis/s1/g1_displacement_strain_reference_analysis_r2_20260810/`；`28cd249` | 关闭第五个 G1 子项；G1 更新为 5/6；仅余三层 |
| 2026-08-10 | G1/three-layer analysis R3 | `rejected` | Codex | 执行证据有效；旧预注册 0.5% 门下 Al `|ΔV0|=0.7660177%` 为唯一 hard rejection | `analysis/s1/g1_three_layer_analysis_r3_20260810/`；`73b3589` | 保留历史拒绝；不得事后改写旧阈值 |
| 2026-08-10 | G1/three-layer follow-up R5 | `accepted` | Codex | 351–358 8/8；4 个 strain 和两个端点 k/cutoff/压力门全通过；0 新重算 | `analysis/s1/g1_three_layer_al_domain_followup_analysis_r5_20260810/`；`5401943` | 排除小应变与端点欠收敛，不单独覆盖 EOS rejection |
| 2026-08-11 | G1/Al acceptance policy R1 | `accepted` | Codex | 用户授权 `|ΔV0|≤1%`；R3/R5 全量重放、17/17 gate、0 solver；范围限制全部硬编码 | `analysis/s1/g1_al_acceptance_policy_r1_20260811/`；`d0386418` | 第六项关闭；G1 6/6，S1 accepted；下一闸门 G2 |
| 2026-08-11 | G2a/G2b periodic-tiling scale/geometry R3 | `accepted` | Codex | 23 函数固定；原胞几何 7/7、32/108 原子平铺 14/14；密度 L2 P95 `0.573791%`、WT P95 `4.035124 meV/atom`；0 solver | `analysis/s2/g2_al_scale_geometry_r3_20260811/`；`472b6c35` | 仅关闭周期平铺规模/几何子门；G2 overall 保持 in_progress，转入局域 108 原子/eggbox/秩连续性 |
| 2026-08-11 | G2a/G2b localized 108-atom R1/R2 | `rejected` | Codex | R1 solver 收敛但单原子 parser 假阴性；R2 零重算恢复。密度/算子/eggbox 能量通过；总秩最低 `2171/2173`，伪力 `0.008360315>0.002 eV/Å` | `analysis/s2/g2_al_localized_analysis_r2_20260811/`；R1 closure `66ca1d76`；R2 evidence `aeacc5a6` | 固定 23 函数候选不适用于当前局域大胞门；G2 overall 继续 in_progress，新 revision 修正冗余/导数连续性 |

## 5. 实验台账

实验 ID 格式：`S阶段-YYYYMMDD-三位序号`，例如 `S1-20260806-001`。每个实验必须有不可变配置和结果目录。

| 实验 ID | 日期 | 阶段 | 假设/目的 | 代码提交 | 配置路径 | 结果路径 | 主要指标 | 结论 | 是否复现 |
|---|---|---|---|---|---|---|---|---|---|
| S0-20260805-001 | 2026-08-05 | S0 | 验证 ABACUS/WT fcc Al 可运行且重复确定 | `f8b619fc551c0f007c2249d23912fccd0363a1d9`；`s0-smoke-20260805` | `tests/smoke/al_fcc_wt/` | `runs/S0-20260805-001/` | 两次 SCF 收敛；能量差 0.0 meV/atom | 通过 | 是，2 次 |
| S0-20260805-002 | 2026-08-05 | S0 | 验证无用户环境变量时可按仓库入口恢复执行 | `5c6a63423c3c0cca0ac46002fa1df9212b480a6e`；`s0-isolated-smoke-20260805` | `tests/smoke/al_fcc_wt/` | `runs/S0-20260805-002/` | 单测 2/2；两次 SCF 收敛；能量差 0.0 meV/atom；总耗时 12.22 s | 通过 | 是，2 次 |
| S0-20260805-003 | 2026-08-05 | S0 | 验证锁定二进制运行时可从空目录恢复并复现 fcc Al/WT 数值 | `f0efae6e6a269d9030e63c04e26b70dff0a3e254` | `tests/smoke/al_fcc_wt/` | `runs/S0-20260805-003/` | 恢复 11.13 s；单测+双重复 12.33 s；ABACUS `ldd` 旧前缀映射 0；能量差 0.0 meV/atom | 数值/归档恢复通过；whole-runtime 隔离未证明 | 是，2 次 |
| S1-20260805-001 | 2026-08-05 | S1 | Al WT V0 30 Ry primitive-cell pilot | `7f846d07d73a10ff2db715ab7662f91a43ff8f5c` | `inputs/s1/eos_candidates/al/v100/ofdft/` | `runs/S1-20260805-001/` | -57.1834030791 eV/atom；0.7788399 GPa | 通过 | 单次 |
| S1-20260805-002 | 2026-08-05 | S1 | Al WT V0 20 Ry 截断点 | `6d193c8c3f03dd98164f0a2a077d656d2a27860c` | `inputs/s1/convergence_candidates/al/ofdft/cutoff/ecut020/` | `runs/S1-20260805-002/` | 相对 30 Ry：0.011269 meV/atom；0.0000990 GPa | 通过 | 单次 |
| S1-20260805-003 | 2026-08-05 | S1 | Al WT V0 40 Ry 截断点 | `d151e2819678d9220f92b6d694eb71d4f06806f8` | `inputs/s1/convergence_candidates/al/ofdft/cutoff/ecut040/` | `runs/S1-20260805-003/` | 相对 60 Ry：0.000108 meV/atom；0.0000148 GPa | 通过 | 单次 |
| S1-20260805-004 | 2026-08-05 | S1 | Al WT V0 60 Ry 初始严格阈值 | `026931d176e5c914551293b606fc3d8409df1ca0` | `inputs/s1/convergence_candidates/al/ofdft/cutoff/ecut060/` | `runs/S1-20260805-004/` | 200 步未收敛；势范数停于 1.9681e-7 | 失败，保留 | 否 |
| S1-20260805-005 | 2026-08-05 | S1 | S1-R1 后复核 Al WT V0 60 Ry | `f58e3d9346ca1754975625c29d3aec58448807c4` | 同上，已提交阈值修订 | `runs/S1-20260805-005/` | 收敛；-57.1834018508 eV/atom；0.7788049 GPa | 通过 | 是 |
| S1-20260805-006 | 2026-08-05 | S1 | Mg WT V0 30 Ry 截断点 | `3c74231991701b36cd4276b82589dbaa1f3aa2a2` | `inputs/s1/convergence_candidates/mg/ofdft/cutoff/ecut030/` | `runs/S1-20260805-006/` | -24.6368596822 eV/atom；-2.6528749 GPa | 通过 | 单次 |
| S1-20260805-007 | 2026-08-05 | S1 | Mg WT V0 40 Ry 截断点 | `bd86a79115453d180b9f47c4a2200b5c4f1d9cc2` | `inputs/s1/convergence_candidates/mg/ofdft/cutoff/ecut040/` | `runs/S1-20260805-007/` | 相对 30 Ry：0.00000651 meV/atom；0.0000070 GPa | 通过 | 单次 |
| S1-20260805-008 | 2026-08-05 | S1 | Mg WT V0 60 Ry 截断点 | `1f9eca6c9de88e4853505ae3f0061df106ed6d5f` | `inputs/s1/convergence_candidates/mg/ofdft/cutoff/ecut060/` | `runs/S1-20260805-008/` | 收敛；相邻加密通过 | 通过 | 单次 |
| S1-20260805-009 | 2026-08-05 | S1 | Mg WT V0 80 Ry 截断点 | `2d83f2e08858e06981e2559de94ef831a9b13335` | `inputs/s1/convergence_candidates/mg/ofdft/cutoff/ecut080/` | `runs/S1-20260805-009/` | 收敛；-24.6368596887 eV/atom | 通过 | 单次 |
| S1-20260805-010–012 | 2026-08-05 | S1 | Al KS V0 40/60/80 Ry 截断 | 各运行目录记录 | `inputs/s1/convergence_candidates/al/ksdft/cutoff/` | 对应 `runs/` | 40→60：0.125921 meV/atom；60→80：0.009981 | 40 Ry 通过 | 三点 |
| S1-20260805-013–015 | 2026-08-05 | S1 | Mg KS V0 40/60/80 Ry 截断 | 各运行目录记录 | `inputs/s1/convergence_candidates/mg/ksdft/cutoff/` | 对应 `runs/` | 40→60：0.001405 meV/atom；60→80：0.000050 | 40 Ry 通过 | 三点 |
| S1-20260805-016–019 | 2026-08-05 | S1 | Al KS 12³/16³/20³/24³ k 点 | 各运行目录记录 | `inputs/s1/convergence_candidates/al/ksdft/kpoint/` | 对应 `runs/` | 相邻变化 3.662659、4.432064、1.377592 meV/atom | 20³ 通过 | 四点 |
| S1-20260805-020–023 | 2026-08-05 | S1 | Mg KS 12x12x8 至 24x24x16 k 点 | 各运行目录记录 | `inputs/s1/convergence_candidates/mg/ksdft/kpoint/` | 对应 `runs/` | 相邻变化 0.225665、2.035276、0.059513 meV/atom | 20x20x12 通过 | 四点 |
| S1-20260805-024–025 | 2026-08-05 | S1 | Al KS 标准/半展宽 V0 诊断 | 各运行目录记录 | `inputs/s1/convergence_candidates/al/ksdft/smearing/` | 对应 `runs/` | 两点收敛；绝对能量位移 4.634969 meV/atom | 诊断完成，G1 待 EOS | 两点 |
| S1-20260805-026–027 | 2026-08-05 | S1 | Mg KS 标准/半展宽 V0 诊断 | 各运行目录记录 | `inputs/s1/convergence_candidates/mg/ksdft/smearing/` | 对应 `runs/` | 两点收敛；绝对能量位移 4.824088 meV/atom | 诊断完成，G1 待 EOS | 两点 |
| S1-20260805-028 | 2026-08-05 | S1 | Al KS 28³ 零温能量确认 | `e1b414e` | `inputs/s1/convergence_candidates/al/ksdft/kpoint/k028x028x028/` | `runs/S1-20260805-028/` | 24³→28³：0.822250 meV/atom | 通过，选择 24³ | 单次 |
| S1-20260805-029–070 | 2026-08-05 | S1 | Al/Mg OFDFT、标准/半展宽 KSDFT 七点 EOS | 逐点提交；分析 `76dbf43` | `config/S1_eos_run_manifest.tsv` | 对应 `runs/` | 42/42 收敛；6/6 BM3；展宽能量/Veq 双门槛通过 | 核心 EOS `accepted`；G1 仍 pending | 42 次 |
| S1-20260805-071–112 | 2026-08-05 | S1 | Al/Mg OF/KS 下一截断及 KS 下一 k 网格七点复核 | 预注册 `d6ffe59`；raw `300a2aa`；硬化 `9010eed`；分析 `d28126b` | `config/S1_non_equilibrium_run_manifest.tsv` | `runs/S1-20260805-071/`–`runs/S1-20260805-112/`；`analysis/s1/non_equilibrium_convergence_20260805/` | 42/42 收敛；6/6 accepted；六组最大相对能差均通过；cutoff 压力均通过 | S1-R8 `accepted`；G1 六项仍 pending | 42 次 |
| S1-RUNTIME-SMOKE-20260805-074 | 2026-08-05 | S1 | 以 074 冻结输入验证重定位 ABACUS、私有 namespace 与严格 whole-runtime 审计入口 | 执行代码 `22eff38`；证据提交 `92e513f` | `analysis/s1/runtime_relocation_smoke_20260805/summary.json` | `analysis/s1/runtime_relocation_smoke_20260805/run/` | 66 个证据文件；能量/压力 `storage_exact`；五类状态门 accepted；三次历史失败归档保留 | `accepted`；幂等重验 `accepted_committed` | 是 |
| S1-20260805-113–118 | 2026-08-05 | S1 | 用重定位 ABACUS 与私有 namespace 复演六个 v100 映射点，复核 whole-runtime 隔离及 R8 结论 | 预注册 `9a0fd7d`；逐点 `8ad4ea8`、`a96896b`、`9800067`、`ce7da88`、`12d2867`、`ce51927`；分析 `a01ac70` | `config/S1_runtime_relocation_equivalence.json`; `config/S1_runtime_relocation_equivalence_manifest.tsv` | `runs/S1-20260805-113/`–`runs/S1-20260805-118/`; `analysis/s1/runtime_relocation_equivalence_20260805/` | 6/6 `storage_exact`；6/6 runtime accepted；每点 22 个登记 ENOENT，成功旧访问/执行/映射和未知探针均为 0；R8 替换结论 6/6 不变 | runtime-relocation equivalence `accepted`；G1 仍 pending | 6 次 |
| S1-20260805-119–148 | 2026-08-05/06 | S1 | 对 90 个已验收基准点独立积分电子数；为 30 个 OF 点输出高精度密度并复核科学/runtime 等价 | R1 accepted 119–129；R2 预注册 `b18106b`；结果终点 `c722c81`；分析 `c94796d` | `config/S1_electron_number_audit.json`; `config/S1_electron_number_audit_r2.json` | `runs/S1-20260805-119/`–`runs/S1-20260805-148/`; `analysis/s1/electron_number_audit_r2_20260805/` | 90/90 accepted；60 KS + 11 R1 reused OF + 19 R2 executed OF；最大认证相对误差 `1.0127696865884852e-11`；30/30 OF 科学等价；KMP 120/120、360/360；R2 新失败 0 | G1 电子数子项 `accepted`；G1 总体 1/6 | R1 复用 11 点 + R2 新执行 19 点 |
| S1-20260806-001–040（R1） | 2026-08-06 | S1 | 第三 smearing/稠密 k 点 thermodynamic-label 审计 | 实现 `64ce08e`；预注册 `f71dd6b`；accepted 终点 `9096ca3`；失败 `df57f9b`；归档 `b0b7db5` | `config/S1_g1_thermodynamic_label_audit_r1.json`; `config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv` | 10 个 active accepted run；`failed_runs/runtime_relocation/S1-20260806-034/attempt-df57f9b610d8/` | P0 4/4、P1 6/8 accepted；034 capability failure/indeterminate；040 与 28 个 P2 点未执行；最终 40/42/6-fit 总门不可判定 | R1 `indeterminate_paused`；G1 仍 1/6 | 10 个 accepted 可在新协议显式复用；R1 ID 全部禁止重跑 |
| S1-20260806-041–070（R2） | 2026-08-06 | S1 | 复用 R1 10 个 accepted 点，以 30 个新 ID 继续第三 smearing/稠密 k 点标签审计 | 实现 `d73e2ba`；预注册 `329a200`；detachment `99deacd`；marker `314ac53`；raw `ff26667`；archive `f91a300` | `config/S1_g1_thermodynamic_label_audit_r2.json`; `config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv` | `failed_runs/runtime_relocation/S1-20260806-041/attempt-ff26667f881e/`；external state 保留于登记目录 | 041 SCF 9 迭代收敛，22:24.42，报告/期望电子数 4.0/4.0，runtime accepted；R2 parser registration rejected；042–070 未执行 | R2 `rejected/stopped`；无 analysis/completion；G1 仍 1/6 | 041 对 accepted 分母贡献 0；041–070 均禁止在 R2 内重跑 |
| S1-20260807-001–040（标签 R3） | 2026-08-07 | S1 | 用全新 40 点消除历史环境复用歧义 | 实现 `e31f456`；001 `2bf6450`；失败 `56f2dcb`；归档 `20be191`；terminal `0d984d0` | `config/S1_g1_thermodynamic_label_audit_r3.json` | R3 external state 与失败归档只读 | 001 accepted；002 在 solver 前因 stop-state 检查竞争停止；003–040 未运行 | R3 `rejected/stopped`；贡献 0 | 001/002 均禁止重跑 |
| S1-20260807-041–080（标签 R4） | 2026-08-07 | S1 | 修复 stop-confirmed shim 后完整重做第三展宽/稠密 k 标签审计 | completion `c4e98320` | `config/S1_g1_thermodynamic_label_audit_r4.json` | `analysis/s1/g1_thermodynamic_label_audit_r4_20260807/` | 40/40 runs、42/42 scalar、14/14 smearing pairs、6/6 k pairs；runtime 160/160、480/480 | 子项 `accepted`；G1 2/6 | 单次、零失败、committed replay 通过 |
| DFTPY-S1-20260810-001–014 | 2026-08-10 | S1 | 独立 DFTpy 七点 Al/Mg 跨代码 EOS 首轮 | 预注册 `38aaa91` | `config/S1_g1_cross_code_dftpy.json` | external R1 state 与 `analysis_r1` 只读 | 14 点科学计算完成；逐点 stdout 为空且独立积分常数不一致 | 证据 `rejected`；贡献 0 | ID 全部消费且不重试 |
| DFTPY-S1-20260810-015–028 | 2026-08-10 | S1 | 修正日志接管和 DFTpy 网格体积后重做独立跨代码 EOS | 修正 `4643b65`；证据 `f10008d` | 同上及 `environment/dftpy_2.2.0_pylibxc_7.0.0.lock.json` | `analysis/s1/g1_cross_code_dftpy_r2_20260810/`；external R2 state 只读 | 14/14 accepted；Al/Mg EOS RMS `1.13e-5/4.13e-4 meV/atom`；压力和电子数门全通过 | 子项 `accepted`；G1 3/6 | node01 CPU 10，14 个全新 ID |
| S1-G1-REGEN10-20260810-001–010（R1） | 2026-08-10 | S1 | 固定 10 例单命令非破坏再生成首轮 | 预注册 `ca90c29`；失败闭包 `8560974` | `config/S1_g1_regeneration_10_r1.json` | external R1 state 只读 | 001–003 accepted；004 solver/science 通过但 runtime 轮询假阴性；005–010 未运行 | R1 `failed_no_retry`；贡献 0 | 不重试 R1 ID |
| S1-G1-REGEN10-20260810-011–020（R2） | 2026-08-10 | S1 | 用逐 rank proof/ACK barrier 重做固定 10 例 | 预注册 `ea90c3e`；证据 `d57216d` | `config/S1_g1_regeneration_10_r2.json` | `analysis/s1/g1_regeneration_10_r2_20260810/`；external R2 state 只读 | 10/10 accepted；9 项 hard gates；所有科学 replay 最大差 0；最大电子数误差 `5.4543e-12` | 子项 `accepted`；G1 4/6 | node01 CPU 20–23；solver 合计 1490.306 s |
| S1-20260810-201–215（位移/应变 R1+analysis R2） | 2026-08-10 | S1 | Al/Mg 小位移、等体积应变和剪切参考标签 | R1 预注册 `bfb9bfc`；R2 预注册 `49da314`；证据 `28cd249` | `config/S1_g1_displacement_strain_reference_r1.json` | `analysis/s1/g1_displacement_strain_reference_analysis_r2_20260810/`；external R1 state 只读 | solver 15/15；R1 analyzer PBC 假阴性；R2 15/15、7/7、300/300 全通过 | 子项 `accepted`；G1 5/6 | node01 CPU 40–43；solver 合计 4650.795 s |

## 6. 当前指标看板

只填写已经由可追溯实验产生的结果；没有结果时保持 `—`，不得填估计值。

| 指标 | 当前值 | 目标 | 数据集/实验 ID | 状态 |
|---|---:|---:|---|---|
| 锁定二进制归档恢复加数值测试时间 | 23.46 s | <60 min | S0-20260805-003 | 通过；不代表 whole-runtime hermetic |
| 运行时归档生成时间 | 68.70 s | 记录项 | S0-20260805-003 | 通过 |
| 隔离 shell 恢复加测试时间 | 12.22 s | 记录项（不替代从零安装 <60 min） | S0-20260805-002 | 通过 |
| smoke test 重复能量差 | 0.0 meV/atom | <0.1 meV/atom | S0-20260805-001 | 通过 |
| Al WT V0 截断加密能量变化 | 0.011269 meV/atom（20→30 Ry） | <1 meV/atom | S1-20260805-001/002 | 通过 |
| Al WT V0 截断加密压力变化 | 0.0000990 GPa（20→30 Ry） | <0.02 GPa | S1-20260805-001/002 | 通过 |
| Mg WT V0 截断加密能量变化 | 0.00000651 meV/atom（30→40 Ry） | <1 meV/atom | S1-20260805-006/007 | 通过 |
| Mg WT V0 截断加密压力变化 | 0.0000070 GPa（30→40 Ry） | <0.02 GPa | S1-20260805-006/007 | 通过 |
| Al KS V0 截断加密能量变化 | 0.125921 meV/atom（40→60 Ry） | <1 meV/atom | S1-20260805-010/011 | 通过 |
| Mg KS V0 截断加密能量变化 | 0.001405 meV/atom（40→60 Ry） | <1 meV/atom | S1-20260805-013/014 | 通过 |
| Al KS k 点尾部加密变化 | 0.822250 meV/atom（24³→28³，零温外推能量） | <2 meV/atom | S1-20260805-019/028 | 通过 |
| Mg KS k 点尾部加密变化 | 0.059513 meV/atom（20x20x12→24x24x16） | <2 meV/atom | S1-20260805-022/023 | 通过 |
| Al/Mg 核心 EOS 完成率 | 42/42 收敛，6/6 曲线 | 100% | S1-20260805-029–070 | 通过 |
| 六曲线 BM3 最大残差 | 0.017078 meV/atom | <1 meV/atom | `analysis/s1/core_eos_20260805/` | 通过 |
| Al 双展宽最大相对能差 / Veq 差 | 0.135259 meV/atom / 0.027655% | <2 meV/atom / <0.2% | 同上 | 通过 |
| Mg 双展宽最大相对能差 / Veq 差 | 0.205258 meV/atom / 0.031817% | <2 meV/atom / <0.2% | 同上 | 通过 |
| 独立原始输入校验 | 168/168 | 100% | S1-20260805-029–070 | 通过 |
| S1-R8 完成率 / 比较验收 | 42/42 收敛；6/6 accepted | 100% / 6/6 | S1-20260805-071–112；`d28126b` | 通过 |
| Al OF cutoff R8：最大 ΔE / ΔP / BM3 残差 | 0.001538733 meV/atom / 0.0001703 GPa / 0.017076344 meV/atom | <1 / <0.02 / <1 | 同上 | 通过 |
| Al KS cutoff R8：最大 ΔE / ΔP / BM3 残差 | 0.006794400 meV/atom / 0.0071189 GPa / 0.009295506 meV/atom | <1 / <0.02 / <1 | 同上 | 通过 |
| Al KS kmesh R8：最大 ΔE / ΔP / BM3 残差 | 0.392503700 meV/atom / 0.0307428 GPa（诊断） / 0.008623252 meV/atom | <2 / 诊断 / <1 | 同上 | 通过 |
| Mg OF cutoff R8：最大 ΔE / ΔP / BM3 残差 | 0.000007179 meV/atom / 0.0000431 GPa / 0.002466982 meV/atom | <1 / <0.02 / <1 | 同上 | 通过 |
| Mg KS cutoff R8：最大 ΔE / ΔP / BM3 残差 | 0.000705450 meV/atom / 0.0001021 GPa / 0.003863243 meV/atom | <1 / <0.02 / <1 | 同上 | 通过 |
| Mg KS kmesh R8：最大 ΔE / ΔP / BM3 残差 | 0.403149550 meV/atom / 0.0171684 GPa（诊断） / 0.004079008 meV/atom | <2 / 诊断 / <1 | 同上 | 通过 |
| G1 电子数审计覆盖与证据拆分 | 90/90 accepted；84 primary + 6 supplemental；60 KS + 11 R1 reused OF + 19 R2 executed OF | 90/90 且拆分精确一致 | `analysis/s1/electron_number_audit_r2_20260805/summary.json` | 子项 `accepted` |
| G1 电子数最大认证相对误差 | `1.0127696865884852e-11`，源点 `S1-20260805-044` | 每点严格 `<1e-10` | 同上 | 通过 |
| G1 电子数 OF 科学等价 | 30/30；能量/压力 failure 0 | `|ΔE| <0.1 meV/atom`；`|ΔP| <0.02 GPa` | 同上 | 通过 |
| G1 电子数 KMP/runtime | 30/30 OF；120/120 rank lifecycles；360/360 成功 syscall | 精确等于 120/360；旧前缀成功访问/映射、unexpected/unhashed mapping 为 0 | 同上 | 通过 |
| G1 电子数失败清单 | density/scientific/KMP/point failure IDs 均为空；R2 新失败 0 | 四类均为 0 | 同上 | 通过 |
| R1 S1-130 历史失败尝试 | 仅作根因档案；验收分母贡献 0；当前 R2 同 ID 结果计 1 次 | 历史失败不删除、不重复计数 | 同上 | 保留并排除 |
| G1 标签 R1 执行覆盖 | 40 个注册点中 10 accepted、1 indeterminate、29 not run；P0 4/4，P1 6 accepted + 034 indeterminate + 040 not run，P2 0/28 | R1 要求 40/40；未完成不得分析 | S1-20260806-001–040；`b0b7db5` | `paused` |
| G1 标签 R1 034 失败闭包 | `indeterminate`；`workflow_or_runtime_capability_failure`；`runtime_kmp`；71 文件；failure/archive/HEAD tree 一致 | 能力证据缺失不得判 accepted/rejected；失败提交与归档必须相邻 | `df57f9b`→`b0b7db5` | 通过失败处置；R1 停止 |
| G1 标签 R2 执行覆盖 | 30 个新 ID 中 1 个 solver 执行并归档、0 个 R2 formally accepted、29 not run；10 个 R1 reused 未进入最终分析 | 40/40 logical accepted、30/30 R2 new accepted | S1-20260806-041–070；`f91a300` | `rejected/stopped` |
| G1 标签 R2 041 数值/runtime | SCF 9 迭代，最终 DRHO `5.73534e-12`；电子数 4.0/4.0；22:24.42；4/4 rank lifecycle，runtime/counterpart/namespace accepted | 只记录不取代 R2 thermodynamic parser/core acceptance | `failed_runs/runtime_relocation/S1-20260806-041/attempt-ff26667f881e/` | 数值/runtime 通过；R2 验收未通过 |
| G1 标签 R2 041 parser | registration 阶段 `ValueError: configuration execution order differs`；thermodynamic parser 1，core 97，terminal stopped/97 | 真实 30 行 manifest/order 必须通过 solver 前 parser 端到端正路 | `thermodynamic_label_parser.stderr.txt`；terminal SHA `d3b9752f...e0269` | 实现契约 rejection；非热力学恒等式数值失败 |
| G1 标签 R4 覆盖 | 40/40 runs；42/42 scalar；14/14 half-quarter pairs；6/6 k pairs | 分母精确一致且 failure ledger 为空 | `analysis/s1/g1_thermodynamic_label_audit_r4_20260807/summary.json`；`c4e98320` | 子项 `accepted` |
| G1 标签 R4 runtime | 160/160 rank lifecycles；480/480 syscalls；terminal accepted；runner=0 | 全部精确通过 | 同上及 supervisor completion | 通过 |
| G1 DFTpy R2 跨代码覆盖 | 14/14 accepted；Al/Mg 各七点；日志收敛标记 14/14 | 14/14、失败/缺失/重试均 0 | `analysis/s1/g1_cross_code_dftpy_r2_20260810/`；`f10008d` | 子项 `accepted` |
| G1 DFTpy Al：EOS RMS / ΔV0 / 最大 ΔP / 最大 abs(ΔN) | `1.1279e-05 meV/atom` / `3.64e-07%` / `0.001036 GPa` / `3.55e-15` | ≤2 / ≤0.2% / ≤0.05 / <1e-10 | 同上 | 通过 |
| G1 DFTpy Mg：EOS RMS / ΔV0 / 最大 ΔP / 最大 abs(ΔN) | `4.1272e-04 meV/atom` / `8.75e-05%` / `0.000105 GPa` / `5.77e-15` | ≤2 / ≤0.2% / ≤0.05 / <1e-10 | 同上 | 通过 |
| G1 10 例再生成 R2 | 10/10；失败/缺失/跳过/重试 0；9 项 hard gates 全通过 | 10/10、四类失败均 0 | `analysis/s1/g1_regeneration_10_r2_20260810/`；`d57216d` | 子项 `accepted` |
| G1 位移/应变 R2 | 15/15；7/7 pairs；300/300 SHA/size；最大 N 相对误差 `8.3786e-12` | 15/15、7/7、证据全一致、N<1e-10 | `analysis/s1/g1_displacement_strain_reference_analysis_r2_20260810/`；`28cd249` | 子项 `accepted` |
| G1 Al 三层 EOS | `|ΔV0|=0.7660177%`；`|ΔB0|=2.4658526%` | `≤1%`；`≤10%` | R3 `73b3589`；policy `d0386418` | 通过；旧 0.5% rejection 保留 |
| G1 R5 应变/端点 | strain max `0.238321 meV/atom`；两端点 k/cutoff/`|ΔP|` 全过 | strain `≤20`；k `<2`；cutoff `<1 meV/atom`；`|ΔP|<0.02 GPa` | R5 `5401943` | 通过 |
| G1 总体 | 6/6 闭合 | 6/6 | 前五项证据；R3 `73b3589`；R5 `5401943`；policy `d0386418` | `accepted` |
| runtime-relocation 六点科学/R8 等价 | 6/6 `storage_exact`；R8 替换结论 6/6 不变 | `|dE|<0.1 meV/atom`、`|dP|<0.02 GPa`；6/6 不翻转 | S1-20260805-113–118；`a01ac70` | `accepted` |
| whole-runtime 旧前缀隔离 | 074 + 六点均为成功旧访问/执行/映射 0、未知探针 0；每点恰有 22 个登记 ENOENT | 同左；登记探针计数必须精确 | `analysis/s1/runtime_relocation_smoke_20260805/`; `analysis/s1/runtime_relocation_equivalence_20260805/` | 限定 namespace 部署路径 `accepted` |
| S2 固定 23 函数密度投影 L2 P95 | `0.573791%`（7 个原胞几何；32/108 平铺同值） | 扰动 `<2%`；平衡 `<1%` | 规模/几何 R3 `472b6c35` | 通过；仅周期平铺域 |
| S2 固定 KEDF 能量差 P95 | `4.035124 meV/atom` | `<10 meV/atom` | 同上 | 通过；仅周期平铺域 |
| S2 32/108 基组规模 / 系数占比 | `645/2173`；`0.145806%/0.145547%` | 占比 `<30%` 且逐原子误差稳定 | 同上 | 通过 |
| S2 局域 108 密度 L2 / 三算子 / WT 总误差 | `0.467935%` / `1.188731` / `5.741805 meV/atom` | `<2%` / `≤10` / `≤10` | 局域 analysis R2 | 通过 |
| S2 局域 108 eggbox 能量 / 伪力 | `0.374359 meV/atom` / `0.008360315 eV/Å` | `≤1` / `≤0.002` | 同上 | 能量通过；伪力拒绝 |
| S2 局域 108 总秩 / 条件数 / retained margin | 最低 `2171/2173` / 最大 `3.7980e7` / 最低 `263.30` | `2173` / `<1e8` / `≥100` | 同上 | 秩拒绝；其余通过 |
| 自洽成功率 | — | >95% | — | 未测 |
| 力有限差分最大偏差 | — | <1e-3 eV/Å | — | 未测 |
| 应力有限差分最大偏差 | — | <0.05 GPa | — | 未测 |
| ML 自洽成功率 | — | >95% | — | 未测 |
| 1024 原子时间/内存收益 | — | 挑战：2x/3x | — | 未测 |

## 7. 阻塞项与风险

### 当前阻塞项

| ID | 发现日期 | 阻塞描述 | 影响阶段 | 负责人 | 下一动作 | 需要外部决策 | 状态 |
|---|---|---|---|---|---|---|---|
| B-001 | 2026-08-05 | 远端 Git URL 已确定并完成首次同步 | S0/S7 | Codex | 后续提交同步至 GitHub `main` | 否 | `accepted` |
| B-002 | 2026-08-05 | BLPS 已确认 CC BY-ND 4.0；原样共享、格式转换和 HQLPP/OEPP 的许可链仍需分别审计 | S0/S7 | 待填写 | 采用上游下载+固定 commit/SHA-256+归属；公开前核对转换/其他 LPP | 是 | `in_progress` |
| B-003 | 2026-08-05 | 项目许可证尚未选择 | S0/S7 | 项目负责人 | 选择许可证或维持内部研究限制 | 是 | `blocked` |
| B-004 | 2026-08-05 | 已在全新空目录恢复锁定二进制前缀并完成单测/smoke | S0 | Codex | 结果固定于 `S0-20260805-003` | 否 | `accepted` |
| B-005 | 2026-08-05 | node01 直连 GitHub HTTPS 超时，不能在计算节点直接推送 | S0–S7 | Codex | 暂用完整 Git bundle 经跳板机传回本机后原子推送；后续可配置可审计的出口代理 | 否 | `paused` |
| B-006 | 2026-08-05 | 第二套独立 OFDFT 已由 DFTpy 2.2.0 的 14/14 跨代码门关闭；独立第二 KS/QE 仍未具备 | S2+ 外推 | Codex | 不反向否定 Al 限定 G1；若继续 QE，须新协议并明确属于外推能力增强 | 否 | `in_progress` |
| B-007 | 2026-08-05 | 原 runtime-isolation 证据只覆盖 ABACUS `ldd`；恢复 `mpirun` 可转调旧前缀 `prterun`，运行中也有旧路径成功访问 | G0/S1 | Codex | 已以登记的重定位+私有 namespace 协议完成 074 和六点复演；冻结身份改变时重新打开 | 否 | `accepted` |
| B-008 | 2026-08-06 | R1 034 的 SSH/PTY 宿主编排在 namespace 子会话完成前中断，使宿主后置 `host_status`、counterpart 与 result 闭包缺失 | S1/G1 | Codex | 在 R2 预注册中冻结脱离 SSH 生命周期的受管启动、唯一 PID/日志/退出状态和同等失败闭包；R1 不重跑 | 否 | `paused` |
| B-009 | 2026-08-06 | R2 adapter 将 30 个 R2 ID 注入冻结 R1 parser，但 R1 registration validator 仍写死 `len(execution_order)==40`；真实预注册未被执行前正路 parser 回归覆盖 | S1/G1 | Codex | R3 已使用自身 production registration contract；真实生成 40 行正路及 39/41、重复、换序、错误 index、sealed 字节负路均通过；待远端 Linux 和正式 committed registration 复验 | 否 | `in_progress` |

### 活跃风险

| 风险 | 概率 | 影响 | 早期信号 | 缓解措施 | 当前状态 |
|---|---|---|---|---|---|
| 混合基无自由度/性能优势 | 中 | 高 | G2 达标需大量低 G | 分级低 G、补偿基、允许转向网格 | 未验证 |
| AMD-OFDFT 先例使宽泛创新主张失效 | 高 | 高 | 仍以“首次原子中心 OFDFT/Pulay 力”立论 | 聚焦全空间系统收敛、互补低 G、同 KEDF 极限和连续导数 | 已识别，定位已修订 |
| 显式 Gaussian⊕PW 病态或偏慢 | 高 | 高 | 原子/PW 近线性相关、端到端慢于 FFT | 四路线 G2 竞赛；优先原子基+FFT/范围分离；性能闸门淘汰 | 未验证 |
| 系数规范不唯一 | 中 | 高 | 同密度不同能量 | 固定最小范数规范、零空间测试 | 未验证 |
| LPP 不可迁移 | 中 | 高 | EOS/相能系统偏差 | 先 Al/Mg、固定同 LPP 对照 | 未验证 |
| 金属展宽标签热力学不一致 | 高 | 高 | 标量能量稳定但密度/势随 sigma 改变 | 三宽度或密 k 参考；F/TS/估计量/密度/导数同口径 | R1 P0 与部分 P1 正常，但 034 能力闭包失败；等待 R2，未形成科学结论 |
| 锁定归档并非 whole-runtime hermetic | 已发生 | 高 | ABACUS `ldd` 通过但 MPI/UCX 转调或访问旧前缀 | 重定位二进制；私有 namespace 遮蔽旧根；严格 `strace` 门；六点数值复演 | 登记路径已缓解并 accepted；原归档口径限制保留 |
| ML 离线准确但自洽失败 | 高 | 高 | 优化逃离训练流形 | 导数/轨迹/响应联合训练 | 未启动 |
| Pulay 导数不连续 | 中 | 高 | FD 无二阶收敛区 | 固定基维数和可微裁剪 | 未启动 |

## 8. 决策日志

只追加，不删除。重大决定必须说明替代方案和证据。

| 日期 | 决策 ID | 决策 | 原因/证据 | 被否决方案 | 影响 |
|---|---|---|---|---|---|
| 2026-08-05 | D-001 | 将项目改为 S0–S7 闸门式执行 | 文献精读和可行性复核显示三项前沿创新不能同时硬承诺 | 原 WP0–WP6 并列推进 | ML、应力、缺陷改为条件任务 |
| 2026-08-05 | D-002 | Al、Mg 为核心，Li、Na 后置 | 降低 LPP、数据和实现并发风险 | 四元素同步展开 | S1–S4 只要求 Al/Mg |
| 2026-08-05 | D-003 | 固定 KEDF 先于 ML-KEDF | 必须先隔离表示和优化误差 | 直接训练 ML 模型 | G2/G3 成为 ML 前置闸门 |
| 2026-08-05 | D-004 | S0/S1 主基准统一使用 ABACUS v3.11.0-beta.5 | 远端已有经过实际 Al/Mg WT 工作验证的 CPU 构建和隔离运行时 | 立即新装 DFTpy/PROFESS | 降低环境变量，保留后续交叉程序核验 |
| 2026-08-05 | D-005 | 新建独立 `/home/shenwei01/M_OFDFT_periodic_basis` | 旧 Al/Mg 熔化目录含进行中工作且不是 Git 仓库 | 直接在旧目录开发 | 避免覆盖既有成果，便于独立验收 |
| 2026-08-05 | D-006 | 统一归档单位为 Å、eV、eV/atom、eV/Å 和 GPa，并保留原始程序输出 | 防止不同程序原生单位和能量口径混入验收表 | 直接沿用各程序打印单位 | S1 起所有解析结果必须显式声明单位和参考态 |
| 2026-08-05 | D-007 | GitHub 仓库 `Zhenhao526/M_OFDFT_periodic_basis` 作为远端代码库 | 用户指定目标；首次上传已通过 SSH 原子推送完成 | 仅保留 node01 本地 Git | 解除 B-001，后续可从外部获取提交与标签 |
| 2026-08-05 | D-008 | node01 无 GitHub 出口时使用完整 Git bundle 中转，并在三端校验 SHA-256 | node01 HTTPS 连接 20 秒超时；bundle 验证完整且三端哈希一致 | 在计算节点反复直接 push | 不阻塞版本发布，但每次同步必须核验远端 ref |
| 2026-08-05 | D-009 | G0 采用锁定二进制归档的干净恢复验收，不冒充源码重编译 | 原运行时无 Conda 元数据；归档 SHA、ABACUS SHA、动态库解析和数值结果均可审计 | 声称无法复现的联网 Conda 重装 | G0 环境恢复口径明确；源码重编译仍可在 S7 另行验收 |
| 2026-08-05 | D-010 | G0 技术验收通过，许可证和 LPP 再分发限制移交 S7 | G0 六项量化标准全部通过；未选择许可证不影响内部数值基准 | 因发布条款无限期阻塞 S1 | 允许启动内部 S1，禁止未经许可的公开发布 |
| 2026-08-05 | D-011 | Al WT V0 的最小候选截断保留为 20 Ry | 20→30 Ry 能量变化 0.011269 meV/atom、压力变化 0.0000990 GPa，后续加密变化更小 | 未扫描直接沿用 20 Ry | 仍须以非平衡体积 EOS 验证相对能量后才可冻结生产参数 |
| 2026-08-05 | D-012 | 记录 60 Ry 严格阈值失败后，将 WT 停止阈值恢复为审计基线 1e-7/1e-6 | S1-004 在稳定能量/压力下势范数平台为 1.9681e-7；阈值修改先提交后复跑 | 删除失败或事后放宽 G1 精度指标 | S1-005 收敛；G1 能量/压力阈值未改变 |
| 2026-08-05 | D-013 | Mg WT V0 的最小候选截断设为 30 Ry | 30→40 Ry 能量变化 0.00000651 meV/atom、压力变化 0.0000070 GPa，四个扫描点全部收敛 | 直接沿用旧 40 Ry 而不验证 | 须在非平衡 EOS 点复核后才能冻结生产参数 |
| 2026-08-05 | D-014 | Al/Mg KSDFT 截断候选均设为 40 Ry | 两套 40→60 Ry 比较均通过能量和压力门槛 | 沿用未经验证的 60 Ry | 后续 k 点、展宽和 EOS 使用 40/160 Ry |
| 2026-08-05 | D-015 | k 点推荐要求候选之后所有已采样相邻加密均通过 | Al 原三点全部失败；Mg 出现先通过后反弹 | 取第一个孤立通过对 | Al 选择 20³，Mg 选择 20x20x12 |
| 2026-08-05 | D-016 | 原计划 k 点范围失败后分别增加 Al 24³、Mg 24x24x16 | 新点与前一点变化分别为 1.377592、0.059513 meV/atom | 强行接受原最大网格 | 保留失败摘要并形成尾部稳定证据 |
| 2026-08-05 | D-017 | 单体积双展宽只记录绝对能量位移，不判定相对能量门槛 | G1 指标要求 EOS 相对能量和平衡体积；单点无法计算二者 | 将 4.6–4.8 meV 绝对位移误作相对能量失败 | 双展宽均进入 EOS，G1 保持未通过 |
| 2026-08-05 | D-018 | 0 K EOS 主能量口径冻结为 ABACUS `E_KS(sigma->0)`，并撤回 Al 20³ k 网格结论 | 源码与输出确认 `FINAL_ETOT` 是含 `-TS` 的 Helmholtz 自由能；20³→24³ 的零温外推差为 2.024876 meV/atom | 跨展宽继续使用含义模糊的 `energy_ev` | 解析器保留 F、-TS、U、E0；EOS 前新增 Al 28³ |
| 2026-08-05 | D-019 | S1-R7 核心 EOS 固定为 14 个结构状态、42 次计算 | 每个 Al/Mg 七点结构均运行 OF、标准 sigma KS、半 sigma KS；双展宽验收需两条完整曲线 | 将“14 点”误作仅 14 次计算 | 生成 S1-029 至 070；Al/Mg 参数分别为 WT 20/30 Ry、KS 40 Ry、24³/20x20x12 |
| 2026-08-05 | D-020 | 清单使用独立文件描述符且单点 stdin 接 `/dev/null` | 首次只运行 S1-029；MPI 继承管道 stdin 吞掉后续 TSV 行；S1-029 结果正常并保留 | 反复单点手工重启 | 回归测试模拟工作进程消耗 stdin，仍完整执行两行清单 |
| 2026-08-05 | D-021 | Mg v090 离散最低点作形状诊断，不作额外硬门槛 | 三条 Mg BM3 Veq 均严格位于 v090–v094，压力跨零，残差 0.0025–0.0039 meV/atom，独立审计复现 | 用未预注册的“离散最小不得在端点”否决连续区间内极小 | 保留拟合 Veq 在范围内、B0>0 和残差门槛 |
| 2026-08-05 | D-022 | S1-R7 核心 EOS 与双展宽验收 `accepted`，G1 保持 pending | 42/42 收敛；Al/Mg 最大曲线差 0.135259/0.205258 meV/atom，Veq 差 0.027655%/0.031817% | 将核心 EOS 通过误报为完整 G1 通过 | 下一步非平衡截断/k 网格复核，仍需积分电子数、第二程序和 0/10 重生证据 |
| 2026-08-05 | D-023 | 基础创新主张收窄为全空间系统收敛、互补低 G/范围分离、同 KEDF 平面波极限和连续导数 | Ke et al. 2014 AMD-OFDFT 已实现周期原子中心密度、变分优化和 Pulay 力 | 继续宣称“首次原子中心周期 OFDFT/Pulay 力” | 项目书升为 V2.1；论文必须建立 claim-evidence matrix |
| 2026-08-05 | D-024 | 显式原子⊕低 G 不再是预设最终架构 | Sun 2017 与 PySCF 当前指南给出偏慢和严重线性相关反例；range separation 是更强工程类比 | 只实现一条显式混合路线再事后评估 | S2 改为纯 PW、原子+FFT、显式混合、互补/范围分离四路线竞赛 |
| 2026-08-05 | D-025 | 修订 D-018 的命名：`E_KS(sigma->0)` 仅作为 `entropy-corrected estimator`，不称严格 0 K 标签 | Mermin 自由能与展宽分析表明两宽度标量外推不能证明密度/势/力的零温一致性 | 直接把 S1-R7 的 E0 字段用于 ML 标签 | S1-R7 双展宽 EOS 结论仍有效；ML 前必须增加第三宽度或密 k 参考并审计密度/导数 |
| 2026-08-05 | D-026 | G1 增加 KS-NL→KS-L→OF-L 三层验证 | 同 LPP 的 OF-L/KS-L 只能量 KEDF 偏差，会隐藏 LPP 对标准 KS 的物理误差 | 用单一 local PP 对照同时声称实现与物理正确 | DFTpy 作第二 OF、QE 作第二 KS；相对 EOS 为跨代码主量 |
| 2026-08-05 | D-027 | S5 拆为 G5a 可积性/自洽稳定与 G5b 物理增益 | MPN 形成能可优于 WT，但绝对能和密度未一致胜出；多篇大数据工作主要为 post-SCF | 用离线能量 RMSE直接接受 ML-KEDF | G5a 任一硬门失败即终止 ML；Na 降为最终外部验证 |
| 2026-08-05 | D-028 | NVE 从晶胞应力拆成独立 S4C | 能量漂移同时依赖力、SCF 容差和积分器；应力通过不能自动证明 NVE | 在 G4B 以 1 ps 单一漂移数合并验收 | S4C 要求 2–5 ps 及步长减半后的二阶漂移检验 |
| 2026-08-05 | D-029 | optimizer-specific surrogate 只作为物理 ML-KEDF 失败后的独立转向 | Remme & Hamprecht 2026 只保证固定优化器得到参考密度，可避开 \(O(N^3)\) Löwdin，但不保证真实能量、力或压力 | 把密度 surrogate 计作 G5 物理 KEDF 成功 | 独立命名并只验密度/OOD/缩放；不改变 S1-R8 和固定 KEDF 主线 |
| 2026-08-05 | D-030 | S1-R8 固定为 42 个加密点并复用 S1-R7 的 28 个唯一基线点 | 每材料分别比较 OF cutoff、KS cutoff、KS kmesh 六条七点曲线；早期 V100 扫描口径不一致 | 重跑 84 次或混用旧扫描点 | cutoff 原始锚定能差 <1 meV/atom 且压力差 <0.02 GPa；kmesh 能差 <2；均为严格门槛 |
| 2026-08-05 | D-031 | S1-R8 六条非平衡加密比较判为 `accepted`，但 G1 保持 `pending` | 42/42 收敛、6/6 严格门通过；正式证据链 raw `300a2aa`→硬化 `9010eed`→分析 `d28126b` | 用 V0 单点、拟合平滑或部分曲线替代原始七点门 | 固化六组 cutoff/kmesh 指标；G1 六项缺口均不因 R8 通过而豁免 |
| 2026-08-05 | D-032 | 对 G0 runtime-isolation 作勘误：撤回 whole-runtime/hermetic 主张，将隔离子项置为 `paused` | 原“旧前缀 0”仅来自 ABACUS `ldd`；后续追踪发现恢复 `mpirun` 转调旧 `prterun` 且存在旧路径成功访问；数值重复性未因此失效 | 继续把库解析报告外推为整个 MPI/UCX 运行链隔离 | 保留锁定归档、哈希、耗时和数值观察；修复协议、074 smoke、113–118 六点复演后再判隔离 |
| 2026-08-05 | D-033 | runtime 修复采用“仅 RUNPATH 重定位 + 私有 user/mount namespace 遮蔽旧根 + 严格追踪 + 六点映射复演”的分级复核 | 已确认重定位二进制与原二进制 Build ID/`NEEDED`/`LOAD` 一致，差异限于 RUNPATH 字符槽；namespace 可使旧根不可见，但正式 074/六点复演尚未完成 | 仅靠 `LD_LIBRARY_PATH`/`ldd`，或在不遮蔽旧根时把运行成功当隔离成功 | 先修协议，074 smoke 失败只修协议重试；113–118 通过前不得声称修复完成 |
| 2026-08-05 | D-034 | 接受登记的 runtime-relocation + 私有 namespace 部署路径并关闭六点等价复演；G1 保持 pending | 074 与正式六点均通过全部 runtime 门；六点 `storage_exact`，R8 替换结论 6/6 不变；分析 `a01ac70` 无失败 | 把 S0 `ldd` 结果继续当 whole-runtime 证明，或把六点通过误报为完整 G1 通过 | G0/runtime-isolation 限定子项改为 accepted；下一步转入 G1 电子数独立积分审计 |
| 2026-08-06 | D-035 | 采用增量电子数 R2：复用 R1 已接受的 11 点并新执行 19 点；KMP 登记对象必须由 raw create/read/unlink 生命周期证明 | R1 119–129 均可严格重验；旧 S1-130 只因 sampler 捕获合法短命 KMP 对象而失败；R2 对 30 个 OF 点证明 120/120 lifecycles、360/360 syscall | 重试等待 sampler 漏采、追溯改写 R1，或无证据重跑全部 30 点 | R1 证据与失败档案保持不可变；R2 新失败 0；结果终点 `c722c81` |
| 2026-08-06 | D-036 | 接受 G1 独立电子数积分子项，但完整 G1 保持 `pending`（1/6） | 正式分析 `c94796d` 为 90/90 accepted；最大认证相对误差 `1.0127696865884852e-11`；30/30 OF 科学等价及 KMP 总门通过，四类 failure ID 全空 | 用 ABACUS 名义电子数代替独立积分，或把一个子项通过误报为完整 G1 通过 | 下一唯一动作转为预注册并执行第三 smearing/稠密 k 点标签审计；不得进入 S2/ML |
| 2026-08-06 | D-037 | 将 G1 标签审计 R1 判为 `indeterminate_paused` 并永久消费 034；保留 10 个 accepted 点但不形成子项结论 | 034 的 SCF 与 inner runtime audit 完成，SSH/PTY 宿主会话先中断，导致 `host_status`、counterpart、result 缺失；冻结 validator 重算为 capability failure；`df57f9b`/`b0b7db5` 完成相邻失败归档 | 把 late inner audit 升格为 accepted、同 ID 重跑 034、跳过 034 继续 040/P2，或把能力缺失误报为数值 rejection | R1 停止；G1 保持 1/6；继续时必须新 revision + 新 IDs，并显式绑定复用证据 |
| 2026-08-06 | D-038 | G1 标签 R2 的 launcher ambient environment 冻结为 10 键 exact map，所有 Python 进程禁用 user site，runner 只增加 6 个 supervisor binding 键并由登记绝对 Bash 启动 | SSH/login shell 可携带 `BASH_ENV`、`PYTHONPATH`、user-site 和其他未登记状态；R1-034 证明 host orchestration 本身必须进入证据边界 | 继承 `os.environ`、依赖 shebang/PATH 选择 Bash，或只在 solver 内再清理环境 | config 登记 exact map 及 canonical SHA `ef6a2022...6b6`；mutating launcher 在任何写入前 fail closed，launch record 保留同一环境结构 |
| 2026-08-06 | D-039 | G1 标签 R2 的 runner/config/manifest 改为 Linux sealed-memfd 执行边界，且首个 marker parent 必须是 GO/detachment introduction | 复核发现“先验哈希、后按路径打开”仍存在 runner/config/manifest TOCTOU，而额外 clean commit 可将首个 marker 与 GO 拆开 | 仅重复路径哈希，或只在 completion 阶段发现 Git parent 不符 | 固定 FD `200/201/202`、seal mask `15`、GO 13 键并绑定 sealed record SHA；GO 前从 `/proc/<pid>/fd` 复核，首个 solver 前即验 marker parent |
| 2026-08-06 | D-040 | R2 所有科学重放必须贯通 sealed config/manifest；finalize 在消费单次 completion 路径前必须严格验 terminal/journal/launch 并冻结稳定原始字节与首次 HEAD | 第五/六轮反例发现 canonical 路径重开、barrier argv 不一致、JSON `false==0`、terminal schema 和 HEAD 二次捕获可造成不可重试失败 | 仅在最终 validator 拒绝，但允许 O_EXCL completion 路径先被消费 | analyzer/replay/parser 全链传递 202/201；completion 用私有只读快照；strict int/exact key/sequence 及写前 raw+HEAD unchanged 复验；六轮复核无 P0/P1 |
| 2026-08-06 | D-041 | 将 G1 标签 R2 判为 `rejected/stopped`，保留 041 数值/runtime 产物但不计入 accepted 分母，不 finalize 也不在 R2 内继续 | 041 SCF 和 runtime 通过；R2 parser 在注册阶段因 R1 硬编码 40 项而拒绝合法 30 项 order；`ff26667`→`f91a300` 相邻归档，terminal stopped/97 | 将机器类别 `thermodynamic_identity` 误读为数值恒等式失败，同 ID 重跑，删除 external state，或事后把 041 升格 accepted | G1 保持 1/6；唯一下一动作是新 R3 revision + 全新 IDs + 真实预注册 parser 正/负回归；041 是否复用必须在 R3 事先冻结 |
| 2026-08-10 | D-042 | DFTpy R1 只保留为 rejected evidence，不因科学数值良好而事后修补或复用 001–014 | DFTpy 的全局 `STDOUT` 未进入逐点 `run.stdout`，且独立积分使用的 Bohr 常数与 DFTpy 网格不一致；因此收敛和电子数证据链不完整 | 手工补日志、覆盖 R1 state、同 ID 重跑，或仅凭能量接近升格 | 冻结 R1 state；在修正实现预注册后使用全新 R2 ID 015–028 |
| 2026-08-10 | D-043 | 接受 DFTpy R2 独立跨代码子项，G1 从 2/6 更新为 3/6 | 14/14 accepted；两材料七点 EOS、V0、压力和独立电子数均通过；环境/源码/依赖哈希已冻结 | 只跑三点 pilot、用绝对总能常数差作门，或把 DFTpy 与 ABACUS 同实现复演混为一谈 | 固化 `f10008d`；R1/R2 state 只读；继续三层、位移/应变和 10 例再生 |
| 2026-08-10 | D-044 | 三层首批采用 Al 硬门、Mg 兼容性诊断的分层口径 | PseudoDojo Al PBE/3e 可与现有 Al PBE/3e local 曲线作受控比较；Mg PBE/10e 同时改变 XC 与价电子数，不能与旧 Mg LDA-PZ/2e 作硬门 | 把 Mg 跨 XC/zval 差异写成 KS-NL projector 效应，或等待不确定的 QE 环境而停算 | 直接执行 ABACUS engine-controlled P0/P1；Mg 结果明确标为 diagnostic，第二 KS 仍未关闭 |
| 2026-08-10 | D-045 | 再生成 R1 按 `failed_no_retry` 冻结，以全新 R2 proof/ACK barrier 重做并接受 10/10 | R1-004 solver/science 通过，但短进程 `/proc/cmdline` 瞬时为空造成 runtime 轮询假阴性；R2 逐 rank proof+ACK 消除该竞态且 9 项硬门全通过 | 手工补 R1、重试 004、把 001–003 混入新分母，或降低 runtime 门 | R1 贡献 0；接受 `d57216d`，G1 更新为 4/6；R1/R2 state 均只读 |
| 2026-08-10 | D-046 | 位移/应变 R1 solver 保留，拒绝旧 R1 分析；以 analysis-only R2 修复 PBC minimum-image 后接受同一 15 点 | 203 的 STRU 与 cube 原子坐标相差整数晶格矢量，旧 analyzer 直接 Cartesian 相减产生假失败；独立重建证实实际位移与形变均正确 | 重算 15 个 solver、放宽几何阈值，或忽略 203 | R1 solver 15/15 不重算；接受 `28cd249` 的 15/15、7/7、300/300，G1 更新为 5/6 |
| 2026-08-10 | D-047 | 三层最终状态必须拆分为 Al hard status 与 Mg compatibility diagnostic | 预注册科学范围把 Mg 定为 diagnostic-only，但 R1 operational P0 实现联合 Al+Mg；另外 NLPP 与 local BLPS 构造不同，差值不是可证明单调的 projector-only 上界 | 让 Mg diagnostic 否定 Al hard scope，或把登记 PP 对差值称为严格 LPP bias upper bound | R1 不改；若 Mg P0 通过则完成 R1 后新增 analysis-only R2，overall 仅由 Al 派生；术语使用 scheme+construction discrepancy / suitability bound |
| 2026-08-11 | D-048 | 保留三层 R3 在旧 `|ΔV0|≤0.5%` 门下的科学拒绝，不回写历史 | R3 evidence/terminal/validator 有效，唯一 hard rejection 为 `0.7660177%>0.5%`；R5 又证明应变与端点收敛无异常 | 修改旧 summary、删除 rejection，或把 R5 局部门替代 EOS 门 | 旧 R3 继续作为 `evidence_valid_scientific_gate_rejected` 的可复现历史证据 |
| 2026-08-11 | D-049 | 按用户授权将新 revision 的 Al `|ΔV0|` 门改为 `≤1%`，其余阈值保持不变 | `0.7660177%≤1%`；`|ΔB0|=2.46585%≤10%`；BM3、R5 strain/k/cutoff/压力均通过；analysis-only 重放新增 solver 0 | 把旧阈值静默覆盖，或为通过而放宽其他门 | policy R1 committed validator 返回 `accepted_g1_6_of_6`；G1 6/6，S1 accepted |
| 2026-08-11 | D-050 | 最终接受范围仅为 Al ABACUS 同引擎登记 PP 对的 scheme+construction suitability bound | Mg 仍为 diagnostic；OF-L↔KS-L 为 error portrait；第二 KS/QE、projector-only 因果和 G4 未关闭；HQLPP/QE binding smoke 失败且贡献 0 | 声称 D-026/QE、Mg 硬比较或 projector-only 因果已闭合 | 允许进入 S2，但所有范围限制继续写入后续论文/协议 |
| 2026-08-11 | D-051 | S2 采用四路线、三胞规模的可淘汰架构竞赛，并先做 G2a/G2b | 显式 Gaussian+PW 存在线性相关和性能负证据；互补/范围分离更有希望，纯 PW/FFT 必须保留为共同精度参考 | 预设显式混合为最终架构，或直接进入 ML/自洽优化 | 预注册 12 个 case；Al 先行，Mg/G2c/S3 继续受闸门约束 |
| 2026-08-11 | D-052 | 接受 Al 单原子首层 pilot 的有效负结果，下一轮只以新 revision 扩展收敛梯级 | 纯原子路线多门失败；两条低 G 路线密度和三算子门通过但 WT 误差约 15.04 meV/atom；互补化显著改善条件数 | 在原 revision 内加 G 或放宽 10 meV 门，或把密度通过误报为 G2a 通过 | `269fe3a9` 永久只读；G2 保持 in_progress，不进入 32/108 原子、Mg、G2c 或 S3 |
| 2026-08-11 | D-053 | 选择 `r08_eta100_complementary` 作为后续规模/连续性候选 | 48 候选全矩阵中它是按“基函数数→条件数→ID”排序的最小合格互补方案；23 函数且 WT 误差 4.0034 meV/atom | 选择自由度更大的点，或只按单一能量误差挑选显式规范 | 单原子 G2a 收敛子步骤 accepted；G2 overall 仍 in_progress，下一闸门为 32/108 原子连续性 |
| 2026-08-11 | D-054 | 接受固定 23 函数候选的周期平铺规模/几何 pilot，但不把它外推为局域大胞或完整 G2 通过 | 7/7 原胞几何与 14/14 规模案例通过；32/108 冻结低 G 集、电子数、逐原子算子和系数规模稳定 | 在每个变形上重新选 G，或把完美平铺冒充局域 108 原子参考 | 固定 G 集保持不变；G2 overall 继续 in_progress，下一闸门为局域参考、eggbox/伪力和秩连续性 |
| 2026-08-11 | D-055 | 接受局域 108 原子独立参考和 R2 有效负证据，拒绝把固定 23 函数候选推进 G2c | 真实 KS-NL 参考收敛；密度与算子门通过，但五点总秩仅 2171–2172 且最大伪力 0.008360315 eV/Å 超门 | 放宽秩/伪力门、删除 R1 parser 失败，或凭 eggbox 能量通过宣称连续性通过 | R1 state/closure 与 R2 evidence 永久只读；新 revision 优先消除径向冗余并改善平移导数，不进入 G2c/S3 |
| 2026-08-12 | D-056 | 将旧伪力失败分类为 96³ 分析网格混叠主导，但不事后改写 R2 | 128³/16 相位下参考/候选/新增最大伪力为 `7.18e-6/2.61e-6/6.28e-6 eV/Å`；较 96³ 分别下降约 403/3472/980 倍 | 直接放宽旧门，或把事后诊断当成正式验收 | 新 revision 前瞻冻结更密网格；秩失败继续独立处置 |

## 9. 最近可用状态

此节必须始终指向一个可运行、可复现的状态；若暂无则明确写“无”。

- 最近可用状态：S2 局域 analysis R2 worktree `/home/shenwei01/wt_s2_g2_al_localized_analysis_r2_final2_20260811` clean@`aeacc5a6`；其 committed validator 应返回有效科学拒绝。
- 对应环境：`environment/` 的 ABACUS v3.11.0-beta.5 CPU + OpenMPI 5.0.10 + LibXC 7.0.0；独立 OF 环境为 `/home/shenwei01/.local/venvs/m_ofdft-dftpy-2.2.0-py311`，身份见锁文件。
- 已通过测试：此前 S2 架构/Al1/收敛/规模证据保持有效；局域 R2 为 5/5 parser/recovery 回归，108 原子 raw 完整重放，11 个输出逐字重建。科学状态为 rejected，不改写为 accepted。
- 已知失败/暂停：局域 R1 solver 成功后被旧单原子 cube parser 假拒绝；R2 只读恢复后的满秩与伪力失败仍为历史权威结论。后续 128³ 诊断已将伪力问题定位到旧分析网格，但秩 2171–2172/2173 尚未闭合。所有旧 state/ID 禁止重跑。
- 恢复方法：在登记 R2 worktree 用冻结 DFTpy Python 运行 `PYTHONPATH=scripts .../python -s -B scripts/validate_s2_g2_al_localized_analysis_r2.py --require-committed`；验证器应 exit 0 并报告 `evidence_valid_scientific_gate_rejected`。
- 同步方法：node01 对 `codex/r4-execution` 相对 GitHub 已知基线创建并验证增量 bundle，经跳板机传至本机；三端 SHA-256 一致后 fast-forward 推送同名分支，最后以 `git ls-remote` 核验目标 SHA。
- 当前交接点：固定 23 函数候选的 128³ 事后诊断已排除“候选本身导数不连续”作为旧伪力超门的主要解释；下一动作是新 revision 以 128³+正式复现，并处理 1–2 个近冗余模态的连续性。Mg、ML、自洽优化和 G2c 尚未启动。

## 10. 交接说明

### G1 标签 R2 历史执行手册（已消费，仅供审计）

> R2 已在 041 后封口停止。下列命令不得再执行，不得用于 finalize、重启监督器或复用 state；保留它们只是为了还原已执行流程。

以下命令均在计算节点 `/home/shenwei01/M_OFDFT_periodic_basis` 执行；本地先经
`ssh -p 39987 liangkun@180.184.249.155` 进入跳板机，再运行
`ssh -p 2200 shenwei01@localhost`。固定外部状态目录为
`/home/shenwei01/.local/state/m_ofdft/g1_thermodynamic_label_audit_r2_20260806`，不得删除、
复用或换目录绕过单次执行约束。
本手册中的所有外部命令都用 `/usr/bin/env -i` 显式传入已登记的 10 键；
任何裸 `python3`、遗漏 `-s` 或继承式 launcher 调用都不属于正式流程。

1. 确认远端已快进到正式预注册提交、`git status --short` 为空，并运行全量单测与 committed
   validator。随后启动脱离 SSH/PTY 的单次监督进程：

   ```bash
   cd /home/shenwei01/M_OFDFT_periodic_basis
   umask 0022
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/python3 -s scripts/launch_s1_g1_thermodynamic_label_audit_r2.py start \
     --project-root /home/shenwei01/M_OFDFT_periodic_basis
   ```

2. 退出当前 SSH；从一条全新的 SSH 会话复核同一 PID/start-time/boot-id、发送 HUP 探针并
   生成 detachment。detachment 必须作为紧邻预注册的单文件提交：

   ```bash
   cd /home/shenwei01/M_OFDFT_periodic_basis
   umask 0022
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/python3 -s scripts/launch_s1_g1_thermodynamic_label_audit_r2.py verify \
     --project-root /home/shenwei01/M_OFDFT_periodic_basis
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/git add -- orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/detachment.json
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/git commit -m "attest detached G1 thermodynamic-label R2 supervisor"
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/python3 -s scripts/validate_s1_g1_thermodynamic_label_audit_r2.py \
     config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv \
     --config config/S1_g1_thermodynamic_label_audit_r2.json \
     --require-committed --check-detachment-attestation
   ```

3. 只有上一步接受且工作树干净时创建 GO；runner 必须由这个仍存活的监督进程直接派生，禁止
   手工调用 runner：

   ```bash
   umask 0022
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/python3 -s scripts/launch_s1_g1_thermodynamic_label_audit_r2.py go \
     --project-root /home/shenwei01/M_OFDFT_periodic_basis
   ```

4. 监控只读状态与日志，不发送终止信号、不修改仓库、不重启相同 ID：

   ```bash
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/python3 -s scripts/launch_s1_g1_thermodynamic_label_audit_r2.py status
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/tail -n 80 /home/shenwei01/.local/state/m_ofdft/g1_thermodynamic_label_audit_r2_20260806/supervisor.log
   ```

5. 仅在 external `terminal.json` 为 accepted、runner 退出码为 0、科学 summary 为 accepted
   时导入 completion；completion 必须紧邻 analysis 的单文件提交，随后再做最终 committed
   复验：

   ```bash
   cd /home/shenwei01/M_OFDFT_periodic_basis
   umask 0022
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/python3 -s scripts/launch_s1_g1_thermodynamic_label_audit_r2.py finalize \
     --project-root /home/shenwei01/M_OFDFT_periodic_basis
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/git add -- orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/supervisor_completion.json
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/git commit -m "complete G1 thermodynamic-label R2 supervisor evidence"
   /usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
     PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
     PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
     /usr/bin/python3 -s scripts/validate_s1_g1_thermodynamic_label_audit_r2.py \
     config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv \
     --config config/S1_g1_thermodynamic_label_audit_r2.json \
     --require-committed --require-all-runs --require-supervisor-completion
   ```

6. 最终复验后只允许更新本进度 MD、提交交接记录并同步 GitHub。若任一 barrier、单点或监督
   失败，保留自动提交的 terminal evidence，禁止当前 revision 内继续或重试；只更新本进度
   MD，交接到“新协议修订 + 全新实验 ID”的处理流程。

### 接手人第一小时

1. 阅读“十分钟上手摘要”和当前阶段；
2. 检查代码仓库状态，不清理或覆盖他人未提交修改；
3. 使用记录的环境安装命令；
4. 运行最近 smoke test；
5. 对照记录的数值和校验和；
6. 在本文件新增一条接手记录；
7. 只执行“下一项唯一动作”，除非先写明变更决策。

### 交接前检查表

- [x] 工作分支和最近提交已填写；
- [x] 未提交修改及其原因已说明；
- [x] 当前实验 ID、配置和结果路径已填写；
- [x] 成功和失败结果均已入台账；
- [x] 下一项唯一动作明确且不超过一句话；
- [x] 阻塞项标明是否需要外部决策；
- [x] 最近 smoke test 已运行并记录；
- [x] 新成员无需口头信息即可恢复工作。

### 接手/交接记录

| 日期时间 | 类型 | 人员 | 当前阶段 | 最近提交 | smoke test | 说明 |
|---|---|---|---|---|---|---|
| 2026-08-05 | 初始化 | 待填写 | S0 | 无 | 未运行 | 文档体系建立 |
| 2026-08-05 13:11 CST | 执行更新 | Codex | S0 | 标签待建立 | S0-20260805-001 通过 | 远端环境和首个数值基线已建立 |
| 2026-08-05 13:17 CST | 执行更新 | Codex | S0 | `5c6a634`; `s0-isolated-smoke-20260805` | S0-20260805-002 通过 | Git 基线与隔离 shell 恢复证据已固化；等待 G0 外部决策和从零安装验收 |
| 2026-08-05 13:22 CST | 执行更新 | Codex | S0 | `5c6a634`; `s0-isolated-smoke-20260805` | S0-20260805-002 通过 | Git 基线、隔离 shell 恢复证据和复现协议已固化；等待 G0 外部决策和从零安装验收 |
| 2026-08-05 13:26 CST | 交接固化 | Codex | S0 | `s0-handoff-20260805` | S0-20260805-002 通过 | 校验清单全量通过，工作树干净；G0 保持 paused |
| 2026-08-05 14:06 CST | 远端同步 | Codex | S0 | `s0-github-sync-20260805` | S0-20260805-002 通过 | `main` 与 S0 标签已上传 GitHub；B-001 已解除 |
| 2026-08-05 14:11 CST | 上传验收 | Codex | S0 | `s0-upload-complete-20260805` | S0-20260805-002 通过 | GitHub HEAD、`main` 和全部标签已远端核验；登记 node01 出口限制与 bundle 中转方法 |
| 2026-08-05 14:18 CST | G0 验收 | Codex | S1 | `s0-clean-recovery-20260805` | S0-20260805-003 通过 | clean-prefix 恢复与测试 23.46 秒；G0 accepted，进入内部 S1 |
| 2026-08-05 14:32 CST | S1 更新 | Codex | S1 | `s1-al-wt-cutoff-20260805` | S1-005 通过；S1-004 失败保留 | 28 个 EOS 与 14 个截断候选已生成；Al WT V0 推荐 20 Ry 候选 |
| 2026-08-05 14:38 CST | S1 更新 | Codex | S1 | `s1-wt-cutoffs-20260805` | S1-006 至 009 全部通过 | Mg WT V0 推荐 30 Ry 候选；下一步转入 KSDFT 收敛扫描 |
| 2026-08-05 15:26 CST | S1 更新 | Codex | S1 | `s1-ks-convergence-20260805` | S1-010 至 027 除协议门槛比较外均数值收敛 | KS 截断和 k 点已选定；双展宽 V0 诊断完成；下一步双展宽 EOS |
| 2026-08-05 15:42 CST | S1 口径复核 | Codex | S1 | 待提交 S1-R6 | 15/15 单测；既有 KS 日志完成 F/-TS/U/E0 重解析 | 撤回 Al 20³，先运行 28³；Mg 结论不变 |
| 2026-08-05 15:57 CST | S1-R7 | Codex | S1 | `e91fbf7` | S1-028 收敛；24³→28³ 为 0.822250 meV/atom | Al 选择 24³；42 次 EOS 矩阵已生成，待提交运行 |
| 2026-08-05 16:06 CST | S1-R7 执行准备 | Codex | S1 | `c53e030` | 22/22 单测；42 输入独立重生一致；空数据负路径正确拒绝 | 输入、清单、运行器和验收器已冻结；下一步远端执行 S1-029–070 |
| 2026-08-05 17:27 CST | S1-R7 核心 EOS | Codex | S1 | `76dbf43` | 25/25 单测；42/42 收敛；6/6 BM3；168/168 输入校验 | 核心 EOS 和双展宽 accepted；G1 pending；下一步 S1-R8 非平衡收敛复核 |
| 2026-08-05 17:40 CST | 扩大文献与可行性复核 | Codex | S1 | `76dbf43`（仅本地文档更新） | 沿用最近 25/25；本轮无新数值运行 | 13 篇/241 页扩展全文精读；项目书 V2.1；新增 G1/S2/S5/S4C 与 surrogate 转向决策；S1-R8 唯一下一动作不变 |
| 2026-08-05 17:57 CST | S1-R8 预注册 | Codex | S1 | `d6ffe59` | 34/34 单测；42/42 manifest 预检；空结果负路径正确 | 六条七点加密曲线与 S1-071–112 已冻结；下一步远端顺序执行约 2.25 小时 |
| 2026-08-05 20:17 CST | S1-R8 验收与 runtime 勘误 | Codex | S1 | raw `300a2aa`；硬化 `9010eed`；正式分析 `d28126b` | 49/49 单测；42/42 收敛；6/6 accepted；runtime replay 未执行 | 固化六组指标和 G1 六项 pending；G0 runtime-isolation 子项 `paused`；唯一下一动作是修协议→074 smoke→113–118 |
| 2026-08-05 23:44 CST | runtime-relocation 六点闭环 | Codex | S1 | 074 `92e513f`；预注册 `9a0fd7d`；六点终点 `ce51927`；分析 `a01ac70` | 92/92 单测；074 accepted；六点 6/6 `storage_exact`、runtime accepted、R8 结论不变 | G0/runtime-isolation 在登记 namespace 路径内 accepted；G1 仍 pending；下一动作是电子数独立积分审计 |
| 2026-08-06 02:53 CST | G1 电子数 R2 闭环 | Codex | S1 | 预注册 `b18106b`；结果终点 `c722c81`；分析 `c94796d` | 123/123 单测；R1 11/11 + R2 19/19；90/90 accepted；KMP 120/120、360/360 | 仅 G1 电子数子项 accepted，G1 总体 1/6；下一动作是第三 smearing/稠密 k 点标签审计 |
| 2026-08-06 16:02 CST | G1 标签 R1 暂停闭环 | Codex | S1 | 实现 `64ce08e`；预注册 `f71dd6b`；accepted 终点 `9096ca3`；失败 `df57f9b`；归档 `b0b7db5` | 147/147 执行前单测；10 个 run accepted；034 failure/archive validator 通过；R1 无最终 analysis | 034 因 SSH/PTY 宿主后置证据缺失为 indeterminate；R1 停止且 ID 禁止重跑；G1 保持 1/6；下一动作 R2 复用 10 点 + 30 新 ID |
| 2026-08-06 23:00 CST | R2 执行前冻结 | Codex | S1 | 实现 `d73e2ba`；预注册 `329a200`；detachment `99deacd` | 本地 206/206；远端 Linux 206/206；第六轮无 P0/P1 | sealed 200/201/202 科学链、GO/marker 因果门、strict JSON type、barrier argv、completion 稳定字节/HEAD 均已闭环；正式预注册与脱离启动完成 |
| 2026-08-06 23:38 CST | G1 标签 R2 停止闭环 | Codex | S1 | marker `314ac53`；raw `ff26667`；archive `f91a300` | 041 SCF/runtime accepted；failure-archive barrier accepted；terminal stopped/97；042–070 未运行 | parser registration 因写死 40 项拒绝合法 30 项 order；R2 禁止 finalize/重跑；G1 保持 1/6；下一动作为 R3 新 revision/新 IDs + 真实预注册 parser 回归 |
| 2026-08-07 12:39 CST | G1 标签 R3 停止闭环 | Codex | S1 | implementation `e31f456`；accepted `2bf6450`；failure `56f2dcb`；archive `20be191`；barrier `0d984d0` | 001 accepted；002 runtime 早停；terminal stopped/97；003–040 未运行 | 根因为 `State:` 字段名误触发 T/t 检查，maps 在未确认 SIGSTOP 时抓取；早停归档又被旧 validator 误报缺 counterpart；R3 不重试、不 finalize |
| 2026-08-07 | G1 标签 R4 修复与执行准备 | Codex | S1 | `codex/r4-execution` 实现提交 | R4 定向及完整套件 90/90；Linux `/proc` SIGSTOP 集成通过；干跑 40 行/160 输入 | 新 shim 要求 map capture 前后均为 T/t；合法 pre-counterpart 早停按四层状态严格识别；新 ID 041–080，P1 041–052；下一动作是单独预注册并启动 supervisor |
| 2026-08-10 10:25 CST | G1 标签 R4 验收补写 | Codex | S1 | `c4e98320` | 40/40 runs、42/42 scalar、14/14 smearing pairs、6/6 k pairs；completion/replay accepted | R4 子项关闭，G1 更新为 2/6；开始剩余四项 |
| 2026-08-10 11:35 CST | G1 独立 DFTpy 跨代码闭环 | Codex | S1 | 预注册 `38aaa91`；R2 修正 `4643b65`；证据 `f10008d` | R1 证据 rejected 并冻结；R2 14/14 accepted；Al/Mg 七点 EOS、V0、压力、电子数门全部通过 | 独立 OFDFT 子项关闭，G1 更新为 3/6；三个剩余子项在独立远端 worktree 并行执行 |
| 2026-08-10 13:20 CST | G1 再生成与位移/应变闭环 | Codex | S1 | 再生成 R2 `d57216d`；位移/应变 R2 `28cd249`；主线 merge `cb0bf1b`/`690f98b` | 再生成 10/10；位移/应变 15/15、7/7、300/300；两项 committed validators 和独立复核均通过 | 两个子项关闭，G1 更新为 5/6；仅余三层对照 |
| 2026-08-11 17:10 CST | G1 最终验收 | Codex | S1→S2 | R3 `73b3589`；R5 `5401943`；policy `d0386418`；主线 merge `da15ac0` | R3/R5 full replay；17/17 gate；committed validator `accepted_g1_6_of_6`；0 solver | G1 6/6、S1 accepted；范围仅 Al 同引擎登记 PP 对；下一动作进入 S2/G2 |
| 2026-08-11 17:52 CST | S2/G2 架构预注册 | Codex | S2 | 实现 `f84c31b0`；预注册 `cdf90874`；主线 `ad402466` | 7/7 单测；12/12 case；13/13 生成物；validator accepted；0 solver | 四路线×三胞规模已冻结；下一动作是 Al 1 原子投影/算子 pilot |
| 2026-08-11 18:12 CST | S2 Al1 投影/算子 pilot | Codex | S2 | 实现 `022551e4`；预注册 `8f00abf3`；证据 `269fe3a9`；主线 `903f9f28` | 7/7 单测；committed validator accepted；5 输出重建；0 solver | 纯原子淘汰；低 G 两路线仅 WT 门失败；下一动作新 revision 收敛梯级 |
| 2026-08-11 18:47 CST | S2 Al1 基组收敛梯级 | Codex | S2 | 实现 `5eb3ae49`；预注册 `9e561bf4`；证据 `b6985a16`；主线 `5e1bcaa0` | 8/8 单测；48 候选；24/24 规范对；committed validator accepted；0 solver | 选定 23 函数互补候选；下一动作 32/108 原子连续性 pilot |
| 2026-08-11 19:18 CST | S2 固定 23 函数规模/几何 pilot | Codex | S2 | 实现 `c6202428`；预注册 `da16a237`；证据 `472b6c35`；主线 `c001cfde` | 8/8 回归；7/7 原胞几何；32/108 共 14/14；committed validator accepted；0 solver | 周期平铺子门 accepted；下一动作局域 108 原子参考与 eggbox/秩连续性 |
| 2026-08-11 20:12 CST | S2 局域 108 原子 eggbox/秩 pilot | Codex | S2 | R1 prereg `28883776`；failure closure `66ca1d76`；R2 evidence `aeacc5a6` | KS 34 iter/324e/96³；R2 5/5；密度/算子/eggbox 能量通过；秩最低 2171、伪力 0.008360315 拒绝 | 有效科学拒绝；G2 overall 不通过，下一动作新 revision 修正冗余与导数连续性 |
| 2026-08-12 12:10 CST | S2 108 原子网格加密诊断 | Codex | S2 | 96³/128³ 诊断 JSON；新 solver 0 | 两网格各16相位；128³新增伪力 `6.28e-6 eV/Å`，电子数差 `1.14e-13 e` | 支持 96³ 网格混叠归因；事后诊断贡献0，下一步新revision正式复现并解决秩冗余 |

## 11. 文档变更记录

| 日期 | 版本 | 修改人 | 修改内容 |
|---|---|---|---|
| 2026-08-05 | V1.0 | Codex | 创建进度、闸门、实验、决策与交接模板；初始化为 S0 |
| 2026-08-05 | V1.1 | Codex | 记录远端环境、ABACUS/LPP 哈希、单元测试及首个 fcc Al/WT smoke 结果 |
| 2026-08-05 | V1.2 | Codex | 记录两次 Git 基线、隔离 shell 恢复演练、第二个 smoke 实验及剩余 G0 阻塞 |
| 2026-08-05 | V1.3 | Codex | 增补 S0 单位、能量口径、命名、随机种子和文件格式协议，并更新交接起点 |
| 2026-08-05 | V1.4 | Codex | 固化最终交接标签、校验清单通过状态和 G0 暂停结论 |
| 2026-08-05 | V1.5 | Codex | 记录 GitHub 远端、首次上传结果、B-001 解除及新的唯一下一动作 |
| 2026-08-05 | V1.6 | Codex | 记录 GitHub 引用验收、node01 出口限制及经校验 bundle 中转的标准同步路径 |
| 2026-08-05 | V1.7 | Codex | 记录 clean-prefix 恢复、S0-003、G0 accepted 结论及 S1 启动位置 |
| 2026-08-05 | V1.8 | Codex | 记录 S1 协议、候选输入、Al WT 截断扫描、失败保留和 Mg 下一动作 |
| 2026-08-05 | V1.9 | Codex | 记录 Mg WT 截断扫描、Al/Mg 候选截断及 KSDFT 下一动作 |
| 2026-08-05 | V2.0 | Codex | 记录 KS 截断、扩展 k 点尾部稳定判据、双展宽 V0 诊断及双展宽 EOS 下一动作 |
| 2026-08-05 | V2.1 | Codex | 冻结零温外推能量口径，迁移解析结果，撤回 Al 20³ 并登记 28³ 确认动作 |
| 2026-08-05 | V2.2 | Codex | 记录 Al 28³ 通过、24³ 最终选择、S1-R7 参数和 42 次核心 EOS 固定清单 |
| 2026-08-05 | V2.3 | Codex | 记录 S1-R7 可执行提交、22/22 单测、内容寻址校验及服务器唯一下一动作 |
| 2026-08-05 | V2.4 | Codex | 记录 42/42 核心 EOS、六曲线拟合、双展宽验收、独立复核、G1 剩余缺口和 S1-R8 唯一下一动作 |
| 2026-08-05 | V2.5 | Codex | 接入扩大文献调研、AMD-OFDFT 先例、V2.1 项目书、三层基准、展宽标签、S2 架构竞赛及 S5/S4C 新闸门；保持 S1-R8 下一动作 |
| 2026-08-05 | V2.6 | Codex | 补入 2026 surrogate functional 全文、PDF/文本/QA、D-029 转向边界并更新扩展文献统计；保持 S1-R8 下一动作 |
| 2026-08-05 | V2.7 | Codex | 将 V2.1 项目书、两轮复核及两套原创文献研判整理为可移植仓库文档；PDF、全文抽取和 QA 产物因许可与历史体积不入库 |
| 2026-08-05 | V2.8 | Codex | 记录 S1-R8 42 点预注册、固定 ID/基线引用、34/34 单测、严格门槛和服务器唯一执行动作 |
| 2026-08-05 | V2.9 | Codex | 记录 S1-R8 42/42、6/6 accepted 与 `300a2aa`/`9010eed`/`d28126b` 证据链；新增 G0 runtime-isolation 勘误、B-007、D-031–033、六组指标、113–118 预留和严格下一动作；未宣称复演通过 |
| 2026-08-05 | V3.0 | Codex | 记录 074 受管 smoke、正式预注册、S1-113–118 六点逐点提交与 `a01ac70` 6/6 正式分析；限定接受 namespace runtime-isolation 路径，保留原 S0 hermetic 勘误与 G1 pending；下一动作转为电子数独立积分审计 |
| 2026-08-06 | V3.1 | Codex | 记录电子数增量 R2 的 11+19 证据拆分、90/90 正式验收、最大认证误差、KMP 120/360 与零新增失败；只关闭 G1 电子数子项并将总体更新为 1/6，下一动作转为第三 smearing/稠密 k 点标签审计 |
| 2026-08-06 | V3.2 | Codex | 记录标签审计 R1 实现/预注册、10 个 accepted run、034 SSH/PTY 宿主后置闭包失败、`df57f9b`/`b0b7db5` 相邻失败归档与 GitHub 同步；R1 为 indeterminate_paused，G1 保持 1/6，下一动作改为显式复用 10 点并以 30 个新 ID 执行 R2 continuation |
| 2026-08-06 | V3.3 | Codex | 记录 R2 执行前 TOCTOU/Git-parent 复核及 sealed-memfd、GO 13 键、首 marker 即时验收硬化；明确当前尚未预注册、未启动远端 solver |
| 2026-08-06 | V3.4 | Codex | 记录六轮 R2 执行前复核、206/206 本地与 Linux 回归、sealed 科学输入贯通、strict type/barrier 失败闭包以及 finalize 稳定字节/HEAD 复验；下一动作为提交实现父边界并单独预注册 |
| 2026-08-06 | V3.5 | Codex | 记录 R2 实现/预注册/脱离证明，041 的 SCF/runtime 成功与 parser registration rejection，`ff26667`/`f91a300` 相邻失败归档、terminal stopped/97、042–070 未执行及 G1 1/6 不变；禁止 R2 finalize/重跑，下一动作改为 R3 新 revision/新 IDs 与真实 30 行预注册 parser 端到端回归 |
| 2026-08-07 | V3.6 | Codex | 记录 R3-001 accepted、R3-002 runtime race、相邻归档与 terminal barrier；冻结 R3 001/002，不重启、不重试、不 finalize |
| 2026-08-07 | V3.7 | Codex | 增加 R4 stop-confirmed shim、pre-counterpart 失败归档状态机、R3 停止链桥、全新 041–080 映射及 90/90 执行前测试；唯一下一动作是正式预注册并脱离启动 |
| 2026-08-10 | V3.8 | Codex | 补写 R4 40/40 与 completion/replay 验收，G1 从 1/6 更新为 2/6；交接起点转为独立 OFDFT、三层对照、位移/应变及 0/10 再生四项 |
| 2026-08-10 | V3.9 | Codex | 记录 DFTpy R1 证据拒绝与 R2 14/14 正式验收，冻结独立环境/哈希和 EOS/V0/压力/电子数指标；G1 从 2/6 更新为 3/6，并登记三层 Al/Mg 科学边界与剩余三项并行执行入口 |
| 2026-08-10 | V4.0 | Codex | 记录再生成 R1 runtime 假阴性及 R2 10/10 闭环、位移/应变 R1 PBC 分析假阴性及 analysis-only R2 15/15 闭环；G1 从 3/6 更新为 5/6，并登记三层 final scope 拆分要求 |
| 2026-08-11 | V4.1 | Codex | 记录三层 R3 旧 0.5% 科学拒绝、R5 8/8 应变/端点闭包和用户授权的 1% policy R1；committed replay 接受 G1 6/6、S1 accepted，并将下一动作切换为 S2/G2；保留 QE/Mg/G4 等范围限制 |
| 2026-08-11 | V4.2 | Codex | 启动 S2/G2；冻结纯 PW/FFT、原子+FFT、显式低 G、互补/范围分离四路线与 1/32/108 原子 12-case 架构合同；记录 0 solver 预注册及下一步 Al 1 原子 pilot |
| 2026-08-11 | V4.3 | Codex | 记录 Al 单原子四路线 analysis-only pilot：纯原子路线多门失败，显式/互补低 G 路线通过密度与三算子但固定 WT 误差超门；保留有效负证据并切换到新 revision 收敛梯级 |
| 2026-08-11 | V4.4 | Codex | 记录 Al 单原子 48 候选收敛矩阵：原 10 meV WT 门不变，选定 23 函数 `r08_eta100_complementary`；关闭单原子收敛子步骤并把唯一下一动作切换为 32/108 原子连续性 pilot |
| 2026-08-11 | V4.5 | Codex | 固定 23 函数候选，记录 7/7 原胞几何与 32/108 原子 14/14 周期平铺规模 pilot；关闭周期平铺规模/几何子门，但保留局域参考、eggbox/伪力、完整大胞 Gram 和 G2 overall 为未关闭 |
| 2026-08-11 | V4.6 | Codex | 建立真实局域扰动 108 原子 KS-NL 参考；保留 R1 单原子 cube parser 假阴性，以 analysis-only R2 完成秩/eggbox/伪力重放；记录固定 23 函数候选因总秩 2171–2172 与伪力超门而被有效科学拒绝 |
| 2026-08-12 | V4.7 | Codex | 记录 96³→128³周期傅里叶网格加密诊断：16相位对照将参考/候选/新增伪力降至 `10^-6 eV/Å`级，支持旧伪力失败由 96³ 网格混叠主导；保留历史R2拒绝和秩门未闭合状态 |
