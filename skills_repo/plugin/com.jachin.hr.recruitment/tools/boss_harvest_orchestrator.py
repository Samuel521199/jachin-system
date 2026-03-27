"""
编排脚本：委托 atom_inbox_harvester_full_flow 实现收网抓取。
流程：选择职位 → 遍历左侧求职者 → 无简历则求简历，有简历则下载 PDF。
"""
import json
from pathlib import Path

from .atom_inbox_harvester import atom_inbox_harvester_full_flow
from .atom_post_job_boss import load_jd_config, get_jd_select
from .boss_utils import canonicalize_boss_job_select
from .hr_data_paths import (
    PLUGIN_DATA_ROOT,
    hr_data_folder_key_from_canonical_jd_select,
    sanitize_job_folder,
)


def _job_text_from_jd_path(jd_config_path: str) -> str:
    """从 jd_config_path 指向的 jd.json 构建 job_text（Boss「全部职位」匹配用）。优先 jd_select。"""
    if not jd_config_path or not Path(jd_config_path).exists():
        return ""
    jd = load_jd_config(jd_config_path, "")
    return get_jd_select(jd) or ""


def harvest_resume_full_flow(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "",
    jd_config_path: str = "",
    download_to_pending: bool = True,
    max_items: int = 50,
    request_if_no_resume: bool = True,
    filter_tab: str = "全部",
    debug: bool = False,
    use_all_positions: bool = False,
) -> dict:
    """
    收网抓取：选择职位 → 遍历左侧候选人 →
    - 无简历：点击「求简历」
    - 有简历：下载 PDF 到 data/{职位}/pending

    可传 jd_config_path（data/{岗位名}/jd.json）或 job_text。
    use_all_positions=True 时选「全部职位」忽略 job_text（仅适合短时联调）；默认 False 按 job_text 精确选职位。
    前置：Chrome 以 --remote-debugging-port 启动，停留在 Boss 沟通页。
    """
    save_dir = None
    job_folder = ""
    if jd_config_path and Path(jd_config_path).exists():
        p = Path(jd_config_path).resolve()
        try:
            root = PLUGIN_DATA_ROOT.resolve()
            if p.name.lower() == "jd.json" and p.parent.parent == root:
                job_folder = sanitize_job_folder(p.parent.name)
                save_dir = str(PLUGIN_DATA_ROOT / job_folder / "pending")
                if not job_text:
                    job_text = _job_text_from_jd_path(jd_config_path)
        except OSError:
            pass
    job_text = job_text or "资深Golang语言开发_杭州 25-40K"
    if not save_dir and job_text:
        canon = canonicalize_boss_job_select(job_text) or job_text.strip()
        fk = hr_data_folder_key_from_canonical_jd_select(canon)
        if fk:
            job_folder = fk
            save_dir = str(PLUGIN_DATA_ROOT / job_folder / "pending")
    if save_dir:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
    return atom_inbox_harvester_full_flow(
        cdp_url=cdp_url,
        job_text=job_text,
        download_to_pending=download_to_pending,
        max_items=max_items,
        save_dir=save_dir,
        filter_tab=filter_tab,
        request_if_no_resume=request_if_no_resume,
        job_folder=job_folder,
        use_all_positions=use_all_positions,
    )
