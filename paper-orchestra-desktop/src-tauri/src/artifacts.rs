use std::fs;
use std::path::{Component, Path, PathBuf};

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;

use crate::models::RunArtifactsSummary;

pub fn get_run_artifacts_summary(run_dir: &str) -> Result<RunArtifactsSummary, String> {
    let root = Path::new(run_dir);
    if !root.is_dir() {
        return Err(format!("Run directory does not exist: {run_dir}"));
    }

    let pdf_path = root.join("final_paper.pdf");
    let final_pdf_path = if pdf_path.is_file() {
        Some(pdf_path.to_string_lossy().to_string())
    } else {
        None
    };

    let has_outline = root.join("outline.json").is_file();
    let has_citation_map = root
        .join("literature_agent_output")
        .join("citation_map.json")
        .is_file();
    let plotting_results_path = root.join("plotting_results.json");
    let has_plotting_results = plotting_results_path.is_file();
    let has_content_worklog = root
        .join("content_refinement_workdir")
        .join("content_refinement_worklog.json")
        .is_file();
    let has_format_worklog = root
        .join("content_refinement_workdir")
        .join("format_refinement_worklog.json")
        .is_file();
    let has_time_breakdown = root.join("time_breakdown.json").is_file();

    let mut latex_files = Vec::new();
    let latex_dir = root.join("latex_writeup");
    if latex_dir.is_dir() {
        collect_latex_files(&latex_dir, &latex_dir, &mut latex_files);
        latex_files.sort();
    }

    let mut generated_figure_paths = Vec::new();
    if has_plotting_results {
        if let Ok(raw) = fs::read_to_string(&plotting_results_path) {
            if let Ok(serde_json::Value::Array(items)) = serde_json::from_str(&raw) {
                for item in items {
                    if let Some(rel) = item.get("image_path").and_then(|v| v.as_str()) {
                        if let Ok(abs) = resolve_safe_subpath(root, rel) {
                            if abs.is_file() {
                                generated_figure_paths.push(abs.to_string_lossy().to_string());
                            }
                        }
                    }
                }
            }
        }
    }

    let has_usage_report = root.join("usage_report.json").is_file();
    let has_citation_audit = root.join("citation_audit.json").is_file();
    let audit_md = root.join("citation_audit.md");
    let citation_audit_md_path = if audit_md.is_file() {
        Some(audit_md.to_string_lossy().to_string())
    } else {
        None
    };
    let has_references_meta = root
        .join("literature_agent_output")
        .join("references_meta.json")
        .is_file();
    let export_dir = crate::export::export_source_dir(root);
    let latex_export_source = export_dir
        .as_ref()
        .and_then(|dir| dir.file_name().map(|n| n.to_string_lossy().to_string()));
    let cited_bib = root.join(crate::export::CITED_BIB);
    let cited_bib_path = if cited_bib.is_file() {
        Some(cited_bib.to_string_lossy().to_string())
    } else {
        None
    };
    // The same decision the export makes, so the view can say it beforehand.
    let latex_export_bib = export_dir.as_ref().and_then(|src| {
        let entries = crate::export::collect_export_files(src).ok()?;
        crate::export::cited_bib_swap(root, src, &entries).map(|(bib, _)| bib)
    });

    Ok(RunArtifactsSummary {
        run_dir: run_dir.to_string(),
        final_pdf_path,
        has_outline,
        has_citation_map,
        has_plotting_results,
        has_content_worklog,
        has_format_worklog,
        has_time_breakdown,
        latex_files,
        generated_figure_paths,
        has_usage_report,
        has_citation_audit,
        citation_audit_md_path,
        has_references_meta,
        latex_export_source,
        cited_bib_path,
        latex_export_bib,
    })
}

fn collect_latex_files(base: &Path, dir: &Path, out: &mut Vec<String>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let p = entry.path();
        if p.is_dir() {
            collect_latex_files(base, &p, out);
        } else if let Some(ext) = p.extension().and_then(|e| e.to_str()) {
            let lower = ext.to_ascii_lowercase();
            if matches!(lower.as_str(), "tex" | "bib" | "sty" | "md") {
                if let Ok(rel) = p.strip_prefix(base) {
                    out.push(rel.to_string_lossy().replace('\\', "/"));
                }
            }
        }
    }
}

pub fn resolve_safe_subpath(root: &Path, relative_path: &str) -> Result<PathBuf, String> {
    let rel = Path::new(relative_path);
    if rel.is_absolute() {
        return Err("Absolute relative path not permitted".to_string());
    }
    // Apply the Windows rules on every OS: a drive prefix ("C:"), a leading
    // separator or a ".." segment split on either '/' or '\' is rejected, so
    // the check does not depend on which platform runs it.
    let bytes = relative_path.as_bytes();
    if bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' {
        return Err("Absolute relative path not permitted".to_string());
    }
    if relative_path.starts_with(['/', '\\']) {
        return Err("Absolute relative path not permitted".to_string());
    }
    if relative_path.split(['/', '\\']).any(|seg| seg == "..") {
        return Err("Path traversal is not allowed".to_string());
    }
    for comp in rel.components() {
        if matches!(comp, Component::ParentDir | Component::RootDir | Component::Prefix(_)) {
            return Err("Path traversal is not allowed".to_string());
        }
    }
    Ok(root.join(rel))
}

pub fn read_run_text_artifact(run_dir: &str, relative_path: &str) -> Result<String, String> {
    let root = Path::new(run_dir);
    let target = resolve_safe_subpath(root, relative_path)?;
    if !target.is_file() {
        return Err(format!("Artifact not found: {}", target.display()));
    }
    fs::read_to_string(&target).map_err(|e| format!("Failed to read artifact: {e}"))
}

pub fn read_file_as_base64(file_path: &str) -> Result<String, String> {
    let p = Path::new(file_path);
    if !p.is_file() {
        return Err(format!("File not found: {file_path}"));
    }
    let bytes = fs::read(p).map_err(|e| format!("Failed to read file '{file_path}': {e}"))?;
    Ok(BASE64.encode(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_safe_subpath_blocks_traversal() {
        let root = Path::new(r"D:\tmp\run");
        assert!(resolve_safe_subpath(root, "outline.json").is_ok());
        assert!(resolve_safe_subpath(root, "latex_writeup/main.tex").is_ok());
        assert!(resolve_safe_subpath(root, "../secret.txt").is_err());
        assert!(resolve_safe_subpath(root, r"C:\Windows\System32").is_err());
    }

    #[test]
    fn summary_reports_usage_and_audit_files() {
        let dir = std::env::temp_dir().join(format!("po_artifacts_{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(dir.join("latex_writeup")).unwrap();
        let run = dir.to_string_lossy().to_string();

        // an older run: none of the new files, existing fields unchanged
        let s = get_run_artifacts_summary(&run).unwrap();
        assert!(!s.has_usage_report && !s.has_citation_audit && !s.has_references_meta);
        assert_eq!(s.citation_audit_md_path, None);
        assert_eq!(s.latex_export_source, None);
        assert_eq!(s.cited_bib_path, None);
        assert_eq!(s.latex_export_bib, None);
        assert!(s.latex_files.is_empty());

        fs::write(dir.join("usage_report.json"), "{}").unwrap();
        fs::write(dir.join("citation_audit.json"), "{}").unwrap();
        fs::write(dir.join("citation_audit.md"), "# audit").unwrap();
        fs::create_dir_all(dir.join("literature_agent_output")).unwrap();
        fs::write(dir.join("literature_agent_output").join("references_meta.json"), "{}").unwrap();
        fs::write(dir.join("latex_writeup").join("raw_draft_paper.tex"), "\\documentclass{article}").unwrap();
        let s = get_run_artifacts_summary(&run).unwrap();
        assert!(s.has_usage_report && s.has_citation_audit && s.has_references_meta);
        assert!(s.citation_audit_md_path.unwrap().ends_with("citation_audit.md"));
        assert_eq!(s.latex_export_source.as_deref(), Some("latex_writeup"));
        assert_eq!(s.latex_files, vec!["raw_draft_paper.tex".to_string()]);

        let json = serde_json::to_value(get_run_artifacts_summary(&run).unwrap()).unwrap();
        for key in ["hasUsageReport", "hasCitationAudit", "citationAuditMdPath", "hasReferencesMeta", "latexExportSource", "finalPdfPath", "latexFiles", "citedBibPath", "latexExportBib"] {
            assert!(json.get(key).is_some(), "{key}");
        }
        let _ = fs::remove_dir_all(&dir);
    }
}

