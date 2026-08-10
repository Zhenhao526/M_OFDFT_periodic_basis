# S1/G1 Al-domain 三层补充验证协议（R4）

协议版本：`S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R4`
实现提交：`R4_IMPLEMENTATION_COMMIT_TO_FREEZE_AT_PREREGISTRATION`
正式新 ID：`S1-20260810-351..358`。每个 ID 只允许一次正式尝试；失败即停止本 revision，禁止删除、覆盖或重试。

## 1. R3 失败闭包与 parser 回归

R3 runner `d5d302d677622e508c2d4d4e9170a005167555ab` 只尝试了 343。ABACUS 在 356.816 s 后 RC=0，但新 metadata 只有 `phase`/`role`，继承的 R1 parser 无条件读取旧 `requirement`，因此 parser `KeyError`；R3 accepted=0、科学贡献=0，343 永不重试，344--350 永不执行。闭包提交为 `25a6ce9169bac16ec4a0ce3bb47c636b7e54d48b`，闭包 SHA256 为 `4b3478c826d987b424cd5cff0a33b304091cdf48f912997d70aaaa4ba1a78b5a`。

R4 不复用 343 进入正式分母。343 的完整 25-file/12,382,620-byte state 被逐文件复制到独立只读 external fixture；禁止硬链接到 R3 state，source/fixture 必须具有相同 relative SHA-list digest `6ab8a172…f46c9`。R4 parser 只接受 raw `role` 存在且 raw `requirement` 缺失的唯一新 schema；它仅在内存兼容对象中令 `requirement=role` 调用继承 parser，随后从结果删除该键并写出 mapping proof。raw 同时出现旧键、缺 role 或 phase/role 不相容均 hard fail。预注册与正式启动前必须用 fixture 完整重放 log/cube/eig-occ/force/stress/affinity/PP/thermodynamic gates，再运行 analysis diagnostic 与三项负回归。

## 2. 八点冻结设计

| R4 ID | 角色/几何 | cutoff Ry | k mesh |
|---|---|---:|---:|
| 351 | Al tetragonal `+0.005`，绑定 204 | 40/160 | 28³ |
| 352 | Al tetragonal `-0.005`，绑定 205 | 40/160 | 28³ |
| 353 | Al xy shear `+0.005`，绑定 206 | 40/160 | 28³ |
| 354 | Al xy shear `-0.005`，绑定 207 | 40/160 | 28³ |
| 355 | v090 normal/extra-k；common=327 | 40/160 | 32³ |
| 356 | v090 high/extra-k；common=327 | 52/208 | 32³ |
| 357 | v110 normal/extra-k；common=328 | 40/160 | 32³ |
| 358 | v110 high/extra-k；common=328 | 52/208 | 32³ |

351--354 必须从 043 独立按晶格行为矩阵 `A'=A F^T` 重建，Direct 坐标保持不变。tetragonal 使用 `F=diag(1+s,(1+s)^(-1/2),(1+s)^(-1/2))`；shear 使用 `F=I+s e_x⊗e_y`。独立核 det(F)=1、矩阵轴/符号/幅度及与 204--207 geometry payload 字节一致。355--358 的 STRU 分别与 preregistered v090/v110 及 accepted 327/328 字节一致。

所有点固定 PBE、PseudoDojo ONCVPSP-PBE-SR/3e、40/160 或 52/208 Ry、`sigma=0.001837465 Ry`、17 位 density cube、force/stress、fresh start。每点必须核 PP/projector18、`zval*nat=Ne=log=cube=eig_occ`、NBANDS=12/末带 occupation、cube origin/full-axis/atom order、final force/stress 对称性与 trace-pressure、12 个热力学标签和 6 个身份式。

## 3. 父证据与科学门

R1 301--306 recovery barrier 与 continuation R2 327--332 `al_eos` phase 必须逐 marker/result/raw/phase/session/config/manifest/barrier/preflight SHA 重放。科学 strain 门按 301/043 锚定，每项 `<=20 meV/atom`；端点用 301/302/303 与 327/328 构造锚定 k `<2 meV/atom`、cutoff `<1 meV/atom`、`|ΔP|<0.02 GPa`。差分解释仅为同引擎 NLPP/local 的 scheme+construction discrepancy suitability bound，不冒充孤立 LPP bias、第二 KS/QE 闭包或 G4。

## 4. 拓扑、smoke 与证据冻结

固定 node01/package0；四 rank primary OS logical CPU 为 30--33，sysfs `core_id` 为 30--33，thread siblings 为 `[30,106]..[33,109]`，完整 8-logical domain 必须持有 nonblocking locks 并做 sibling-aware collision scan。不得在 raw 中使用歧义 `physical_core_ids`。

R3 accepted smoke `ea160ac…3297` 与 analysis-only closure `62c6aacc…` 只作为相同拓扑算法 bridge；R4 仍必须在正式 state/attempt 之前，用 R4 config SHA、同一 MPI map 和 R4 wrapper 做唯一一次 detached 4-rank config-specific smoke，满足 setsid/session-leader、SIGHUP ignored、无 TTY、4/4、failed0、RC0、ABACUS0。smoke aggregate 必须作为 exact prereg 的唯一 evidence-only child 提交；formal runner 逐字核 external/versioned/committed aggregate 后才允许创建 R4 state。

最终分母必须为 8/8，failed/missing/skipped/retried=0，terminal 逐 ID 强绑 attempt/accepted/runner-return/result/metadata SHA。UPF body 不进入 Git；committed replay 通过 repository/commit/URL/git-blob/SHA/header、pseudo_identity、metadata/result 和 log projector identity 闭合，并由服务器 external cache 验 full body。
