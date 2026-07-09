# GSAS-II Rietveld Refinement Skill

**GitHub description / 仓库简介**

EN: A Codex skill for conservative GSAS-II Rietveld refinement of powder XRD data, final-archive hygiene, and publication-style Python plots.

中文：一个用于粉末 XRD 的 GSAS-II Rietveld 精修 Codex skill，强调稳健精修、最终结果归档和论文风格 Python 精修图。

## English

This repository publishes a Codex skill for careful powder diffraction refinement with GSAS-II. It is designed for research workflows where a lower Rwp is not enough: the agent must inspect residual peaks, reject nonphysical overfitting, preserve source data, and archive only the final defensible refinement package.

The skill is especially useful for:

- GSAS-II / GSAS / EXPGUI-style powder Rietveld refinement.
- Nb14W3O44 and related Wadsley-Roth oxide XRD refinement.
- Comparing conservative and lower-Rwp candidate refinements.
- Generating reproducible Python Rietveld plots directly from final `.gpx` files.
- Cleaning intermediate refinement files after the final archive is verified.

The actual Codex skill lives in:

```text
gsas-ii-rietveld-refinement/
```

Important local configuration:

- Set `GSASII_DIR` to your local `GSAS-II` source directory.
- Set `GSASII_PYTHON` to the Python executable that can import `GSASIIscriptable`.
- Optionally set `GSASII_REFINEMENT_ARCHIVE` and `GSASII_REFINEMENT_STAGING` to control final and temporary output locations.

Example:

```bash
export GSASII_DIR="$HOME/g2main/GSAS-II"
export GSASII_PYTHON="$HOME/g2main/bin/python"
export GSASII_REFINEMENT_ARCHIVE="$HOME/GSAS-II_refinement_results"
export GSASII_REFINEMENT_STAGING="$HOME/GSAS-II_refinement_staging"
```

The repository does not include instrument parameter files, CIF databases, private XRD data, or final refinement results. Bring your own calibrated instrument file and source diffraction data.

## 中文

这个仓库发布的是一个面向 Codex 的 GSAS-II 粉末 XRD 精修 skill。它的核心不是单纯把 Rwp 压低，而是让 agent 按科研上更稳妥的方式工作：检查残差峰、拒绝不合理过拟合、保留原始数据、只把可辩护的最终精修结果归档。

适用场景包括：

- GSAS-II / GSAS / EXPGUI 粉末 Rietveld 精修。
- Nb14W3O44 及相关 Wadsley-Roth 氧化物的 XRD 精修。
- 对比保守模型和低 Rwp 候选模型。
- 从最终 `.gpx` 文件直接生成可复现的 Python 精修图。
- 最终归档验证后清理中间过程文件。

真正的 Codex skill 位于：

```text
gsas-ii-rietveld-refinement/
```

本地使用前建议配置：

- `GSASII_DIR`：本机 `GSAS-II` 源码目录。
- `GSASII_PYTHON`：能导入 `GSASIIscriptable` 的 Python 解释器。
- `GSASII_REFINEMENT_ARCHIVE`：最终精修结果归档目录，可选。
- `GSASII_REFINEMENT_STAGING`：精修过程暂存目录，可选。

示例：

```bash
export GSASII_DIR="$HOME/g2main/GSAS-II"
export GSASII_PYTHON="$HOME/g2main/bin/python"
export GSASII_REFINEMENT_ARCHIVE="$HOME/GSAS-II_refinement_results"
export GSASII_REFINEMENT_STAGING="$HOME/GSAS-II_refinement_staging"
```

仓库不包含仪器参数文件、CIF 数据库、私人 XRD 数据或最终精修结果。实际使用时请提供自己的校准仪器文件和原始衍射数据。

## License / 许可证

No license has been specified yet. Add a license before inviting public reuse.

目前尚未指定开源许可证。如果希望他人正式复用，请在发布前补充许可证。
