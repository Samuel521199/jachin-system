#!/usr/bin/env python3
"""生成 data/hr_resumes/test_resume.pdf，供 HR 透析镜 PDF 能力测试。"""
from pathlib import Path

try:
    from fpdf import FPDF
except ImportError:
    print("请先安装: pip install fpdf2")
    raise SystemExit(1)

# 纯 ASCII 内容，确保任意环境可生成并提取
RESUME_TEXT = """
Test Resume - PDF Format

Basic Info
- Name: Test User
- Role: Backend Engineer
- Email: test@example.com

Tech Stack
- Proficient: Python, Go, Rust
- Familiar: Kubernetes, Redis, PostgreSQL

Work Experience
- 2022-2024: Led microservices architecture for user center
- 2020-2022: Built CI/CD and monitoring from scratch

Projects
- E-commerce platform: 10M+ daily requests
- Internal MCP Agent orchestration

Summary
Passionate about engineering and team collaboration.
""".strip()


def main() -> None:
    proj = Path(__file__).resolve().parent.parent
    out = proj / "data" / "hr_resumes" / "test_resume.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    pdf.set_auto_page_break(True, margin=15)
    for line in RESUME_TEXT.split("\n"):
        pdf.cell(0, 8, line, ln=True)
    pdf.output(str(out))
    print(f"已生成: {out}")


if __name__ == "__main__":
    main()
