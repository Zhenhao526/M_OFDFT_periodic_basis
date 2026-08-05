# M_OFDFT_periodic_basis

周期金属原子/混合密度基 M-OFDFT 项目的远端执行仓库。

当前阶段：S1（平面波 OFDFT/KSDFT 基准闭环）。G0 已通过；Al/Mg 的 WT
及 KSDFT 截断扫描已完成，正在执行 k 点与展宽收敛。尚未开始混合密度基或
ML-KEDF 开发。

## 固定远端位置

```text
/home/shenwei01/M_OFDFT_periodic_basis
```

## 基线软件

- ABACUS：v3.11.0-beta.5
- 二进制：`/home/shenwei01/wt_melting_runtime_20260724/build-abacus-wt-cpu/source/abacus_pw_para`
- 运行时：`/home/shenwei01/wt_melting_runtime_20260724/conda_prefix`
- MPI：OpenMPI 5.0.10
- LibXC：7.0.0

精确哈希见 `manifests/SOFTWARE_SHA256SUMS`，完整包列表见 `environment/conda_prefix_packages.txt`。

## 干净二进制环境恢复

基线前缀没有 Conda 元数据，因此 G0 使用“已锁定二进制归档恢复”而非不可审计的
联网重解算。归档包含完整依赖前缀和已校验的 ABACUS 二进制；恢复脚本拒绝覆盖
已有目录，先校验归档和 ABACUS SHA-256，再检查动态库不得回落到原基线前缀。

```bash
cd /home/shenwei01/M_OFDFT_periodic_basis
./scripts/restore_runtime.sh \
  /home/shenwei01/M_OFDFT_runtime_20260805.tar.gz \
  /home/shenwei01/M_OFDFT_recovery_S0_20260805_001

export M_OFDFT_RUNTIME=/home/shenwei01/M_OFDFT_recovery_S0_20260805_001
export M_OFDFT_ABACUS="$M_OFDFT_RUNTIME/source/abacus_pw_para"
./scripts/run_unit_tests.sh
./scripts/run_smoke.sh S0-20260805-003
```

`run_smoke.sh` 默认使用基线运行时；设置上述两个环境变量后，MPI 和全部动态库
必须来自新恢复目录。该验收证明锁定二进制环境可从空目录恢复，不等同于 ABACUS
源码重编译。

## 快速测试

```bash
cd /home/shenwei01/M_OFDFT_periodic_basis
./scripts/run_unit_tests.sh
./scripts/run_smoke.sh S0-20260805-001
```

smoke test 会对同一 fcc Al/WT 输入运行两次，要求：

- 两次均出现 `#SCF IS CONVERGED#`；
- 两次总能差小于 0.1 meV/atom；
- 结果写入 `runs/<experiment-id>/smoke_result.json`；
- 若实验目录已存在则拒绝覆盖。

## 目录

```text
assets/pseudo/       固定赝势副本及校验和
docs/                项目书、进度和远端审计
environment/         系统与运行环境锁
manifests/           软件、输入和项目文件校验和
runs/                不可覆盖的实验目录
scripts/             测试、执行和结果检查
tests/smoke/         fcc Al/WT smoke 输入模板
tests/unit/          解析器与协议单元测试
```

## 修改纪律

1. 每次实验使用唯一 ID：`S阶段-YYYYMMDD-三位序号`。
2. 不覆盖既有 `runs/` 目录。
3. 每次工作结束前更新本地与远端的 `M_OFDFT_项目进度与交接.md`。
4. 任何结果必须记录 ABACUS、赝势、输入和代码提交哈希。
5. 许可证尚待项目负责人决定；在此之前不得对外发布本仓库内容。
