use std::fs;
use std::path::Path;

use chrono::Utc;

use crate::models::{SaveSkillInput, SkillEntry};

struct BuiltinSkill {
    id: &'static str,
    name: &'static str,
    description: &'static str,
    category: &'static str,
    content: &'static str,
}

const BUILTIN_SKILLS: &[BuiltinSkill] = &[
    BuiltinSkill {
        id: "builtin-paper-deep-read",
        name: "论文六步精读 SOP",
        description: "按 ClawsGO 论文精读 6 步工作流拆解 PDF：材料完整度、全篇概览、章节任务链、关键公式/段落精读、图文一致检查、结构图与审稿批注表。",
        category: "阅读与选题",
        content: "---\nname: paper-deep-read\ndescription: 六步论文精读 SOP\n---\n\n请对给定文献执行六步精读：\n1. 材料完整性检查（缺附录/代码/补充材料需显式标出）。\n2. 全文核心问题、主宣称、方法亮点与主结论概览。\n3. 章节职责与逻辑关联表。\n4. 核心段落与关键方程精读（注明假设条件与实验边界）。\n5. 图文一致核查与批判性审稿清单。\n6. 给出 Mermaid 结构框图。",
    },
    BuiltinSkill {
        id: "builtin-lit-review-gbt7714",
        name: "防编造主题式综述 · GB/T 7714",
        description: "先写文献梳理与证据矩阵，再定研究背景；严格区分作者自身结论与转引结论，标明全文/仅摘要阅读深度并采用 GB/T 7714 顺序引文。",
        category: "文献综述",
        content: "---\nname: lit-review-gbt7714\ndescription: 防编造主题式综述与 GB/T 7714 引用\n---\n\n按以下铁律撰写文献综述：\n1. 先梳理文献分析与统一证据对比矩阵，再写研究背景与问题提出。\n2. 正文仅引用真实核验过的条目，严禁编造 DOI/年份/作者。\n3. 长段落连贯学术叙述，禁止两三句话碎段列表体。\n4. 列出可逐句点回来源的出处映射表与 GB/T 7714 参考文献列表。",
    },
    BuiltinSkill {
        id: "builtin-data-analysis-7step",
        name: "七步可复现数据分析规范",
        description: "输出数据字典、可逆清洗日志、描述统计、带效应量与 95% 置信区间的假设检验，并按「现象-证据-解释-局限」四层总结。",
        category: "数据分析",
        content: "---\nname: data-analysis-7step\ndescription: 七步可复现数据分析与四层报告\n---\n\n执行七步数据分析检查：\n1. 明确问题 -> 数据字典 -> 可逆清洗 -> 描述统计 -> 选图绘图 -> 关系/差异检验(报告 n、精确 p、效应量、95% CI) -> 四层结构(现象/证据/解释/局限)。\n严禁重复试错挑显著结果。",
    },
    BuiltinSkill {
        id: "builtin-paper-reproduction",
        name: "算法复现七步检查表",
        description: "确定复现层级边界、拆分可独立测试的模块、记录数据/代码哈希、建立最小闭环、对齐评价指标语义、完成消融对照。",
        category: "实验与复现",
        content: "---\nname: paper-reproduction\ndescription: 文献复现七步工程 SOP\n---\n\n按照七步复现法组织实验文档：\n1. 复现目标与边界声明；2. 算法模块化工程规格；3. 数据版本与哈希清单（缺失数据说明替代验证边界）；4. 最小闭环代码；5. 指标语义对齐（阈值/均值方式）；6. 三层核验对照；7. 可运行 README。",
    },
    BuiltinSkill {
        id: "builtin-nature-de-ai-polish",
        name: "Nature 级去 AI 味英文润色",
        description: "控制破折号/分号预算，消除空洞套话，首句给结论而非防御性铺垫，确保图注首句加粗总标题及统计学报告完整。",
        category: "写作与投稿",
        content: "---\nname: nature-de-ai-polish\ndescription: Nature Portfolio 风格与去 AI 味润色\n---\n\n逐段审修并润色英文学术手稿：\n- 摘要零破折号零分号；正文每段最多一处。\n- 删除 delve / tapestry / realm / seamless / Taken together / It is worth noting 等模板套话。\n- 核对每张图的正文引用与图注首句加粗标题、样本量 n 和误差棒定义是否完整。",
    },
];

fn skills_dir(app_data_dir: &Path) -> std::path::PathBuf {
    app_data_dir.join("skills")
}

fn sanitize_skill_id(raw: &str) -> String {
    let s: String = raw
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '-' | '_') {
                c.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect();
    let trimmed = s.trim_matches('-').to_string();
    if trimmed.is_empty() {
        format!("skill-{}", Utc::now().timestamp())
    } else {
        trimmed
    }
}

fn parse_skill_markdown(id: &str, raw: &str, updated_at: &str) -> SkillEntry {
    let mut name = id.to_string();
    let mut description = String::new();
    let mut category = "自定义技能".to_string();

    if let Some(rest) = raw.strip_prefix("---") {
        if let Some(end_idx) = rest.find("\n---") {
            let fm = &rest[..end_idx];
            for line in fm.lines() {
                if let Some((k, v)) = line.split_once(':') {
                    let key = k.trim().to_ascii_lowercase();
                    let val = v.trim().trim_matches('"').trim_matches('\'').to_string();
                    if key == "name" && !val.is_empty() {
                        name = val;
                    } else if key == "description" {
                        description = val;
                    } else if key == "category" && !val.is_empty() {
                        category = val;
                    }
                }
            }
        }
    }
    SkillEntry {
        id: id.to_string(),
        name,
        description,
        category,
        content: raw.to_string(),
        builtin: false,
        updated_at: updated_at.to_string(),
    }
}

pub fn list_skills(app_data_dir: &Path) -> Result<Vec<SkillEntry>, String> {
    let mut items: Vec<SkillEntry> = BUILTIN_SKILLS
        .iter()
        .map(|b| SkillEntry {
            id: b.id.to_string(),
            name: b.name.to_string(),
            description: b.description.to_string(),
            category: b.category.to_string(),
            content: b.content.to_string(),
            builtin: true,
            updated_at: "内置".to_string(),
        })
        .collect();

    let dir = skills_dir(app_data_dir);
    if dir.is_dir() {
        let mut custom = Vec::new();
        for entry in fs::read_dir(&dir).map_err(|e| format!("读取技能目录失败：{e}"))? {
            let entry = entry.map_err(|e| format!("遍历技能目录失败：{e}"))?;
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("md") {
                continue;
            }
            let Some(stem) = path.file_stem().and_then(|s| s.to_str()) else {
                continue;
            };
            let raw = match fs::read_to_string(&path) {
                Ok(t) => t,
                Err(_) => continue,
            };
            let updated = fs::metadata(&path)
                .and_then(|m| m.modified())
                .map(|t| chrono::DateTime::<Utc>::from(t).to_rfc3339())
                .unwrap_or_default();
            custom.push(parse_skill_markdown(stem, &raw, &updated));
        }
        custom.sort_by(|a, b| a.name.cmp(&b.name));
        items.extend(custom);
    }
    Ok(items)
}

pub fn save_skill(app_data_dir: &Path, input: &SaveSkillInput) -> Result<SkillEntry, String> {
    let name = input.name.trim();
    if name.is_empty() {
        return Err("技能名称不能为空。".to_string());
    }
    if input.content.trim().is_empty() {
        return Err("技能内容（Prompt / Markdown）不能为空。".to_string());
    }
    let dir = skills_dir(app_data_dir);
    fs::create_dir_all(&dir).map_err(|e| format!("创建技能目录失败：{e}"))?;

    let mut id = input
        .id
        .as_deref()
        .map(sanitize_skill_id)
        .filter(|s| !s.is_empty() && !s.starts_with("builtin-"))
        .unwrap_or_else(|| sanitize_skill_id(name));
    if id.starts_with("builtin-") {
        id = format!("custom-{id}");
    }

    let body = if input.content.trim_start().starts_with("---") {
        input.content.clone()
    } else {
        let cat = if input.category.trim().is_empty() {
            "自定义技能"
        } else {
            input.category.trim()
        };
        format!(
            "---\nname: \"{}\"\ndescription: \"{}\"\ncategory: \"{}\"\n---\n\n{}",
            name.replace('"', "'"),
            input.description.trim().replace('"', "'"),
            cat.replace('"', "'"),
            input.content.trim()
        )
    };
    let path = dir.join(format!("{id}.md"));
    fs::write(&path, body.as_bytes()).map_err(|e| format!("保存技能文件失败：{e}"))?;
    let now = Utc::now().to_rfc3339();
    Ok(SkillEntry {
        id,
        name: name.to_string(),
        description: input.description.trim().to_string(),
        category: if input.category.trim().is_empty() {
            "自定义技能".to_string()
        } else {
            input.category.trim().to_string()
        },
        content: body,
        builtin: false,
        updated_at: now,
    })
}

pub fn delete_skill(app_data_dir: &Path, skill_id: &str) -> Result<(), String> {
    if skill_id.starts_with("builtin-") {
        return Err("内置技能不可删除。".to_string());
    }
    let safe = sanitize_skill_id(skill_id);
    let path = skills_dir(app_data_dir).join(format!("{safe}.md"));
    if path.is_file() {
        fs::remove_file(&path).map_err(|e| format!("删除技能失败：{e}"))?;
    }
    Ok(())
}

pub fn resolve_skill_by_id(app_data_dir: &Path, skill_id: &str) -> Result<SkillEntry, String> {
    list_skills(app_data_dir)?
        .into_iter()
        .find(|s| s.id == skill_id)
        .ok_or_else(|| format!("找不到技能：{skill_id}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    #[test]
    fn lists_builtins_and_roundtrips_custom_skill() {
        let dir = std::env::temp_dir().join(format!("po_skills_test_{}", Uuid::new_v4()));
        let list = list_skills(&dir).unwrap();
        assert!(list.iter().filter(|s| s.builtin).count() >= 5);

        let saved = save_skill(
            &dir,
            &SaveSkillInput {
                id: None,
                name: "Chemistry Test Skill".to_string(),
                description: "Checks stoichiometry".to_string(),
                category: "化学".to_string(),
                content: "Check all equations carefully.".to_string(),
            },
        )
        .unwrap();
        assert!(!saved.builtin);

        let after = list_skills(&dir).unwrap();
        assert!(after.iter().any(|s| s.id == saved.id && s.category == "化学"));
        assert!(delete_skill(&dir, "builtin-paper-deep-read").is_err());
        delete_skill(&dir, &saved.id).unwrap();
        let _ = fs::remove_dir_all(&dir);
    }
}
