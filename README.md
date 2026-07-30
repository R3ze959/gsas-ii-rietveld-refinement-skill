# GSAS-II Rietveld Refinement Skill

**GitHub description / 仓库简介**

EN: A conservative Codex skill for deterministic GSAS-II powder XRD Rietveld refinement, with branched geometry sensitivity, residual auditing, metric validation, and transactional result archiving.

中文：一个面向真实 GSAS-II 粉末 XRD Rietveld 精修的 Codex skill，强调确定性分支精修、晶胞/Zero 敏感性、残差审查、指标校验和事务型归档；不负责画图。

## Update: Refinement and Plotting Are Now Separate / 更新：精修与绘图拆分

This release narrows the skill to refinement, validation, and archival work.
Rietveld figure generation and restyling have moved to the independent
`rietveld-plotting` skill. The split prevents presentation choices from
changing refinement parameters and makes each workflow easier to audit.

本次更新将此 skill 明确限定为精修、校验与归档。Rietveld 图的生成和样式调整已
拆分到独立的 `rietveld-plotting` skill。这样可以避免绘图阶段意外修改精修参数，
也便于分别复核数值结果与图形表达。

This release also adds:

- a deterministic cell/Zero branch matrix instead of a single release order;
- a retained `candidate_summary.json` with hashes and fit diagnostics;
- automatic R-factor label and covariance-derived uncertainty validation;
- transactional archive replacement with rollback and hash verification;
- portable configuration with no private data or machine-specific paths.

本次更新还加入：

- 确定性的晶胞/Zero 分支比较，不再只走一条依次释放路径；
- 保留含哈希和拟合诊断的 `candidate_summary.json`；
- 自动核验 R 因子标签及由协方差生成的不确定度；
- 带回滚和哈希复核的事务型归档替换；
- 不含私人数据或个人电脑绝对路径的可移植配置。

## English

This repository publishes a Codex skill that turns GSAS-II refinement into a disciplined, research-facing workflow. It is built for situations where a low Rwp is not enough: the agent must use real GSAS-II, test refinement candidates in stages, inspect residual peaks, reject nonphysical overfitting, keep source data traceable, and archive only the final defensible refinement package.

The skill is designed for powder XRD workflows in materials research, especially crystalline materials where phase models, site occupancies, peak broadening, and residual peaks need cautious interpretation. It helps Codex behave less like an automatic curve-fitting script and more like a careful refinement assistant that records why a model should or should not be trusted.

### What This Skill Does

- Runs real GSAS-II workflows through `GSASIIscriptable`; it does not present simulated patterns as refinement.
- Builds `.gpx` projects from XRD data, CIF structure models, and instrument parameter files.
- Runs a fixed branch matrix after scale/background: cell-only, Zero-only, simultaneous cell+Zero, cell-then-Zero, and Zero-then-cell.
- Locks instrument U/V/W first and releases profile terms only after geometry sensitivity; uncalibrated free U/V/W is refused.
- Keeps chemically sensitive parameters restrained by default, including atomic coordinates, occupancies, dopant sites, and oxygen deficiency.
- Audits the full-range observed-minus-calculated curve before accepting a final result.
- Compares conservative and lower-Rwp candidates, then rejects unstable, correlated, or nonphysical fits.
- Requires a short dialectical review so each candidate is judged by fit quality, chemistry, residual shape, and reviewer risk.
- Retains `candidate_summary.json` with source hashes, correlations, Durbin-Watson values, residual maxima, and failed branches.
- Generates and validates canonical reports with unambiguous R-bkg/RF labels and covariance-derived `value(esd)` formatting.
- Archives the exact XRD, instrument file, source/result CIF, GPX/LST, report, and audit JSON transactionally: validate, copy to a sibling temporary directory, hash-check, and atomically install or replace.
- Does not plot; final `.gpx` files are handed to the separate `rietveld-plotting` skill when requested.
- Cleans current-run intermediate files only after the final archive is verified, without deleting original XRD or CIF inputs.

### Best For

- GSAS-II / GSAS / EXPGUI-style powder Rietveld refinement.
- CIF-based refinement of crystalline materials with complex unit cells, mixed sites, dopants, defects, or uncertain impurity phases.
- Comparing safe conservative models against exploratory lower-Rwp models.
- Preparing clean refinement folders for papers, group meetings, or follow-up crystallographic review.
- Avoiding common overclaims such as proving dopant site occupancy or oxygen deficiency from lab Cu Kalpha XRD alone.

### What It Does Not Include

- GSAS-II itself.
- Instrument parameter files.
- CIF databases.
- Private XRD data.
- Final refinement results.

Bring your own GSAS-II installation, calibrated instrument file, source CIF, and diffraction data.

## 中文

这个仓库发布的是一个面向 Codex 的 GSAS-II 粉末 XRD 精修 skill。它的目标不是机械地把 Rwp 压低，而是把 GSAS-II 精修变成一个更接近科研判断的流程：真实调用 GSAS-II、分阶段测试候选模型、检查残差峰、拒绝不合理过拟合、保留原始数据来源，并且只归档最终可辩护的精修结果。

它适合材料体系的粉末 XRD 精修，尤其是相模型、位点占位、峰展宽和残差峰都需要谨慎解释的复杂晶体材料。这个 skill 会让 Codex 不只是“自动拟合曲线”，而是像一个谨慎的精修助手一样，记录为什么某个模型可信、为什么某个低 Rwp 结果应该被拒绝。

### 主要功能

- 通过 `GSASIIscriptable` 调用真实 GSAS-II；不把模拟图谱包装成精修结果。
- 根据 XRD 数据、CIF 结构模型和仪器参数文件建立 `.gpx` 项目。
- 在 scale/background 后固定运行晶胞-only、Zero-only、晶胞+Zero 同时、晶胞后 Zero、Zero 后晶胞五条分支。
- 先锁定仪器 U/V/W，再根据几何敏感性决定是否测试峰形；未标定仪器禁止自由 U/V/W。
- 默认约束化学敏感参数，包括原子坐标、占位、掺杂位点和氧缺陷。
- 在接受最终结果前审查全范围 observed-minus-calculated 残差曲线。
- 对比保守模型和低 Rwp 候选模型，拒绝不稳定、高相关或非物理的拟合。
- 要求写出简短的“辩证审查”，从拟合质量、化学合理性、残差形状和审稿风险判断候选模型。
- 保留 `candidate_summary.json`，记录源文件哈希、相关矩阵、Durbin–Watson、残差峰和失败分支。
- 自动生成并校验规范报告，明确区分 R-bkg 与 RF，并从协方差生成 `value(esd)`。
- 将精确 XRD、仪器文件、源/结果 CIF、GPX/LST、报告和审计 JSON 事务化归档：先验证、复制到同级临时目录、哈希复核，再原子安装或替换。
- 本 skill 不画图；需要图时把最终 `.gpx` 交给独立的 `rietveld-plotting` skill。
- 只在最终归档验证后清理当前 run 的中间文件，不删除原始 XRD 或 CIF 输入。

### 适用场景

- GSAS-II / GSAS / EXPGUI 粉末 Rietveld 精修。
- 基于 CIF 的复杂晶体材料精修，包括复杂晶胞、混合位点、掺杂、缺陷或不确定杂相。
- 对比保守模型和探索性低 Rwp 模型。
- 为论文、组会汇报或后续晶体学复核整理干净的精修文件夹。
- 避免从普通实验室 Cu Kalpha XRD 中过度声称掺杂位点、氧缺陷或定量相含量。

### 仓库不包含

- GSAS-II 软件本体。
- 仪器参数文件。
- CIF 数据库。
- 私人 XRD 数据。
- 最终精修结果。

实际使用时请准备自己的 GSAS-II 安装、校准仪器文件、源 CIF 和原始衍射数据。

## Skill Location / Skill 位置

The actual Codex skill lives in:

```text
gsas-ii-rietveld-refinement/
```

真正的 Codex skill 位于：

```text
gsas-ii-rietveld-refinement/
```

## Local Configuration / 本地配置

Important local configuration:

- Set `GSASII_DIR` to your local `GSAS-II` source directory.
- Set `GSASII_PYTHON` to the Python executable that can import `GSASIIscriptable`.
- Optionally set `GSASII_REFINEMENT_ARCHIVE` and `GSASII_REFINEMENT_STAGING` to control final and temporary output locations.

本地使用前建议配置：

- `GSASII_DIR`：本机 `GSAS-II` 源码目录。
- `GSASII_PYTHON`：能导入 `GSASIIscriptable` 的 Python 解释器。
- `GSASII_REFINEMENT_ARCHIVE`：最终精修结果归档目录，可选。
- `GSASII_REFINEMENT_STAGING`：精修过程暂存目录，可选。

Example / 示例：

```bash
export GSASII_DIR="/path/to/GSAS-II"
export GSASII_PYTHON="/path/to/gsas-python"
export GSASII_REFINEMENT_ARCHIVE="$HOME/GSAS-II_refinement_results"
export GSASII_REFINEMENT_STAGING="$HOME/GSAS-II_refinement_staging"
```

## License / 许可证

Released under the MIT License. See [LICENSE](LICENSE).

本项目采用 MIT 许可证发布，详见 [LICENSE](LICENSE)。
