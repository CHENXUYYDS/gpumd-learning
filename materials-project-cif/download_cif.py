#!/usr/bin/env python3
"""按元素从 Materials Project 批量导出 CIF；默认包含全部子体系。"""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import importlib.metadata
import io
from itertools import combinations
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

VERSION = "1.0.2"
ELEMENTS = tuple("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())
SYMBOLS = frozenset(ELEMENTS)
MODE_NAMES = {"subsystems": "全部子体系", "exact": "仅完整元素体系", "contains": "含全部指定元素，允许其他元素"}
META_FIELDS = ["material_id", "formula_pretty", "chemsys", "elements", "nsites", "symmetry", "deprecated", "is_stable", "energy_above_hull", "last_updated"]
CSV_FIELDS = ["material_id", "formula", "chemsys", "nsites", "spacegroup", "is_stable", "energy_above_hull_eV_per_atom", "deprecated", "status", "cif_file", "sha256", "error", "url"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_elements(values: list[str]) -> list[str]:
    text = " ".join(values).strip()
    if not text:
        raise ValueError("请至少输入一个元素，例如 Si，或者 Li Fe O。")
    tokens = re.split(r"[\s,，、;；\-]+", text)
    normalized = []
    for token in tokens:
        if not token:
            continue
        symbol = token if token in SYMBOLS else token.capitalize() if token.islower() else token
        if symbol not in SYMBOLS:
            raise ValueError(f"无法识别元素 {token!r}。请用元素符号并用空格分开，例如 Li Fe O；不要输入 LiFePO4 这样的化学式。")
        normalized.append(symbol)
    return sorted(set(normalized))


def build_criteria(elements: list[str], mode: str, include_deprecated: bool, stable_only: bool, max_systems: int = 4095) -> dict[str, Any]:
    criteria: dict[str, Any] = {"deprecated": None if include_deprecated else False}
    if mode == "subsystems":
        total = (1 << len(elements)) - 1
        if total > max_systems:
            raise ValueError(f"这组元素有 {total} 个非空子体系，超过 --max-systems {max_systems}。请减少输入元素；如确实需要完整枚举，可显式增大 --max-systems。不会截断查询结果。")
        # 服务端 elements/exclude_elements 字符串最多 60 字符。
        # 精确枚举化学体系，不发送所有其他元素的长排除列表。
        criteria["chemsys"] = ["-".join(group) for size in range(1, len(elements) + 1)
                               for group in combinations(elements, size)]
    elif mode == "exact":
        criteria["chemsys"] = "-".join(elements)
    else:
        if len(",".join(elements)) > 60:
            raise ValueError("contains 模式的元素列表超过 API 的 60 字符限制，请减少输入元素。")
        criteria["elements"] = elements
    if stable_only:
        criteria["is_stable"] = True
    return criteria


def metadata_batches(criteria: dict[str, Any]) -> list[dict[str, Any]]:
    """按体系数和请求长度分批，每批仍取齐所有结果分页。"""
    systems = criteria.get("chemsys")
    if not isinstance(systems, list):
        return [criteria]
    batches = []
    batch: list[str] = []
    length = 0
    for system in systems:
        added = len(system) + bool(batch)
        if batch and (len(batch) >= 50 or length + added > 500):
            batches.append({**criteria, "chemsys": batch})
            batch, length = [], 0
        batch.append(system)
        length += len(system) + (len(batch) > 1)
    if batch:
        batches.append({**criteria, "chemsys": batch})
    return batches


def checked_elements(doc: dict[str, Any], requested: list[str], mode: str) -> list[str]:
    found = sorted(str(e) for e in doc.get("elements", []))
    wanted, present = set(requested), set(found)
    valid = bool(present)
    valid = valid and (present <= wanted if mode == "subsystems" else present == wanted if mode == "exact" else wanted <= present)
    if not valid:
        raise ValueError(f"API 返回了范围外或无元素信息的材料 {doc.get('material_id')}；已停止，避免下载范围错误。")
    return found


def checked_material_id(value: Any) -> str:
    material_id = str(value)
    # MP 现行 AlphaID 是小写字母；保留旧数字 ID 的兼容性。
    # 明确限制字符，避免把返回字段直接当成任意文件路径。
    if not re.fullmatch(r"(?:mp|mvc)-(?:[0-9]+|[a-z]+)", material_id):
        raise ValueError(f"不支持的材料编号格式：{material_id!r}")
    return material_id


def material_identity(value: Any) -> tuple[str, int]:
    """仅用于匹配同一材料的数字/字母/补齐字母别名，原编号仍原样保存。"""
    material_id = checked_material_id(value)
    prefix, body = material_id.split("-", 1)
    if body.isdecimal():
        return prefix, int(body)
    number = 0
    for letter in body:
        number = number * 26 + ord(letter) - ord("a")
    return prefix, number


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mp-cif-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_error(exc: Exception, api_key: str) -> str:
    return str(exc).replace(api_key, "[API_KEY_HIDDEN]") if api_key else str(exc)


def retry_call(call, attempts: int, api_key: str):
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            message = clean_error(exc, api_key)
            if re.search(r"\b(401|403)\b|invalid api key|api key is invalid|unauthorized|forbidden", message, re.I):
                raise RuntimeError("API 认证或访问被拒绝，请检查 MP_API_KEY 和账号权限。") from None
            status = re.search(r"(?:status(?:\s+code)?|HTTP(?:Error)?)[\s:=]*(400|404|405|413|414|422)\b", message, re.I)
            if status:
                raise RuntimeError(f"API 拒绝了请求参数（HTTP {status.group(1)}），重试相同参数无法解决。\n{message}") from None
            if attempt + 1 == attempts:
                raise RuntimeError(message) from None
            delay = min(2 ** attempt, 8)
            print(f"请求未成功，{delay} 秒后重试（{attempt + 1}/{attempts}）。", flush=True)
            time.sleep(delay)


@contextmanager
def output_lock(folder: Path):
    """进程退出时由操作系统释放锁，保留锁文件不影响下次续传。"""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".download.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("同一个输出目录已有下载进程，请等待它结束或使用其他输出目录。") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def new_record(doc: dict[str, Any], elements: list[str], mode: str) -> dict[str, Any]:
    present = checked_elements(doc, elements, mode)
    material_id = checked_material_id(doc.get("material_id", ""))
    formula = str(doc.get("formula_pretty") or material_id)
    safe_formula = re.sub(r"[^A-Za-z0-9_\-]", "_", formula)[:100]
    chemsys = "-".join(present)
    symmetry = doc.get("symmetry") or {}
    return {
        "material_id": material_id,
        "formula": formula,
        "chemsys": chemsys,
        "elements": present,
        "nsites": doc.get("nsites"),
        "spacegroup": symmetry.get("symbol") if isinstance(symmetry, dict) else str(symmetry),
        "is_stable": doc.get("is_stable"),
        "energy_above_hull_eV_per_atom": doc.get("energy_above_hull"),
        "deprecated": doc.get("deprecated"),
        "last_updated": str(doc.get("last_updated") or ""),
        "status": "pending", "sha256": "", "error": "",
        "cif_file": f"cif/{chemsys}/{safe_formula}__{material_id}.cif",
        "url": f"https://materialsproject.org/materials/{material_id}",
    }


def save_state(folder: Path, state: dict[str, Any]) -> None:
    state["checkpoint_utc"] = utc_now()
    atomic_text(folder / "download_state.json", json.dumps(state, ensure_ascii=False, indent=2, default=str) + "\n")


def write_reports(folder: Path, state: dict[str, Any]) -> dict[str, int]:
    rows = state["records"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(folder / "materials.csv", "\ufeff" + output.getvalue())
    counts = {key: sum(r["status"] == key for r in rows) for key in ("downloaded", "pending", "failed")}
    report = {
        "version": VERSION, "created_utc": utc_now(), "query": state["query"],
        "query_utc": state["query_utc"], "matched_materials": len(rows), **counts,
        "all_matched_cifs_present": counts["downloaded"] == len(rows),
        "systems_with_results": sorted({r["chemsys"] for r in rows}),
        "structure_scope": "One summary structure per matching material ID; no initial/task history.",
    }
    atomic_text(folder / "report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    failures = [{"material_id": r["material_id"], "error": r["error"]} for r in rows if r["status"] == "failed"]
    atomic_text(folder / "failed.json", json.dumps(failures, ensure_ascii=False, indent=2) + "\n")
    return counts


def query_snapshot(summary, folder: Path, query: dict[str, Any], args, api_key: str) -> dict[str, Any]:
    checkpoint = folder / "download_state.json"
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if state.get("schema") != "mp-cif.v1" or state.get("query") != query:
            raise RuntimeError("输出目录属于其他检索条件，请使用 --output 指定一个新目录。")
        if not isinstance(state.get("records"), list):
            raise RuntimeError("下载清单损坏，请保留原文件并使用新的输出目录。")
        print(f"读取已有材料清单：{len(state['records'])} 条，将校验文件并续传。", flush=True)
        return state
    if any(p.name != ".download.lock" for p in folder.iterdir()):
        raise RuntimeError("输出目录已有其他文件且没有本工具的下载清单，请换一个空目录，避免覆盖已有资料。")
    print("正在查询全部匹配材料编号（自动读取所有分页）……", flush=True)
    criteria = build_criteria(query["elements"], query["mode"], query["include_deprecated"], query["stable_only"], getattr(args, "max_systems", 4095))
    batches = metadata_batches(criteria)
    if query["mode"] == "subsystems":
        print(f"共有 {len(criteria['chemsys'])} 个非空子体系，分 {len(batches)} 批查询。", flush=True)
    records = {}
    for number, batch in enumerate(batches, start=1):
        docs = retry_call(lambda batch=batch: summary.search(**batch, fields=META_FIELDS, all_fields=False, num_chunks=None, chunk_size=500), args.retries, api_key)
        for doc in docs:
            if not isinstance(doc, dict):
                raise RuntimeError("API 客户端没有返回字典数据，请按 requirements.txt 安装匹配版本。")
            record = new_record(doc, query["elements"], query["mode"])
            # 元素范围与弃用/稳定性条件再核对一次，避免服务端过滤异常。
            if not query["include_deprecated"] and record["deprecated"] is True:
                raise RuntimeError("API 返回了已弃用材料，请检查接口过滤条件。")
            if query["stable_only"] and record["is_stable"] is not True:
                raise RuntimeError("API 返回了稳定性筛选范围外的材料。")
            records.setdefault(material_identity(record["material_id"]), record)
        print(f"材料查询：{number}/{len(batches)} 批，累计 {len(records)} 个编号。", flush=True)
        if number < len(batches):
            time.sleep(0.25)
    try:
        client_version = importlib.metadata.version("mp-api")
    except importlib.metadata.PackageNotFoundError:
        client_version = "not-installed/offline-test"
    state = {"schema": "mp-cif.v1", "query": query, "query_utc": utc_now(), "mp_api_version": client_version,
             "records": sorted(records.values(), key=lambda row: row["material_id"])}
    save_state(folder, state)
    return state


def managed_path(folder: Path, relative: str) -> Path:
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()) or path.suffix.lower() != ".cif":
        raise RuntimeError("下载清单包含无效的 CIF 路径，请使用新输出目录。")
    return path


def download(summary, folder: Path, query: dict[str, Any], args, api_key: str, Structure, CifWriter) -> int:
    with output_lock(folder):
        state = query_snapshot(summary, folder, query, args, api_key)
        rows = state["records"]
        print(f"找到 {len(rows)} 个材料，覆盖 {len({r['chemsys'] for r in rows})} 个有数据的化学子体系。", flush=True)
        pending = []
        for row in rows:
            path = managed_path(folder, row["cif_file"])
            if row["sha256"] and path.is_file() and sha256_file(path) == row["sha256"]:
                row["status"], row["error"] = "downloaded", ""
            else:
                row["status"], row["error"] = "pending", ""
                pending.append(row)
        save_state(folder, state)
        if args.list_only:
            write_reports(folder, state)
            print(f"仅生成清单，未下载 CIF：{folder / 'materials.csv'}")
            return 0
        skipped = len(rows) - len(pending)
        print(f"已校验并跳过 {skipped} 个完整 CIF；还需下载 {len(pending)} 个。", flush=True)
        try:
            for start in range(0, len(pending), args.batch_size):
                batch = pending[start:start + args.batch_size]
                ids = [r["material_id"] for r in batch]
                try:
                    docs = retry_call(lambda: summary.search(material_ids=ids, deprecated=None,
                        fields=["material_id", "structure"], all_fields=False, num_chunks=None,
                        chunk_size=args.batch_size), args.retries, api_key)
                    structures = {material_identity(d["material_id"]): d.get("structure") for d in docs}
                    if set(structures) - {material_identity(idx) for idx in ids}:
                        raise RuntimeError("结构接口返回了本批次之外的材料编号。")
                except Exception as exc:
                    message = clean_error(exc, api_key)
                    for row in batch:
                        row["status"], row["error"] = "failed", message
                    save_state(folder, state)
                    raise RuntimeError("本批结构请求失败，已保留清单和成功文件；再次运行相同命令即可续传。\n" + message) from None
                for row in batch:
                    try:
                        raw = structures.get(material_identity(row["material_id"]))
                        if raw is None:
                            raise ValueError("接口未返回此编号的结构，可能在当前 API 中不可用。")
                        structure = Structure.from_dict(raw) if isinstance(raw, dict) else raw
                        actual = {str(e) for e in structure.composition.elements}
                        if actual != set(row["elements"]):
                            raise ValueError("结构中的元素与材料清单不一致。")
                        if row["nsites"] is not None and len(structure) != row["nsites"]:
                            raise ValueError("结构原子数与材料清单不一致，请用新目录重新查询。")
                        text = str(CifWriter(structure, symprec=None, significant_figures=12, refine_struct=False))
                        # 保留原晶胞/坐标；不做对称化、扩胞或晶胞标准化。
                        path = managed_path(folder, row["cif_file"])
                        atomic_text(path, text if text.endswith("\n") else text + "\n")
                        row["sha256"] = sha256_file(path)
                        row["status"], row["error"] = "downloaded", ""
                    except Exception as exc:
                        row["status"], row["error"] = "failed", clean_error(exc, api_key)
                        print(f"导出失败：{row['material_id']}；详见 failed.json。", flush=True)
                    save_state(folder, state)
                write_reports(folder, state)
                completed = sum(r["status"] == "downloaded" for r in rows)
                print(f"进度：完整 CIF {completed}/{len(rows)}；本批处理结束。", flush=True)
                time.sleep(0.25)
        finally:
            save_state(folder, state)
            counts = write_reports(folder, state)
        print(f"输出目录：{folder}\n完整 CIF：{counts['downloaded']}；失败：{counts['failed']}；待下载：{counts['pending']}。", flush=True)
        return 0 if not counts["failed"] and not counts["pending"] else 1


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("elements", nargs="*", help="元素符号，例如 Li Fe O；不填则交互输入")
    parser.add_argument("--mode", choices=tuple(MODE_NAMES), default="subsystems", help="检索范围")
    parser.add_argument("-o", "--output", type=Path, help="输出目录；默认在当前目录建立 MP_CIF_元素_模式")
    parser.add_argument("--include-deprecated", action="store_true", help="同时检索当前 API 仍能访问的已弃用条目")
    parser.add_argument("--stable-only", action="store_true", help="只下载 MP 标记为稳定的材料；默认不限制稳定性")
    parser.add_argument("--list-only", action="store_true", help="只查询并保存材料清单，不下载 CIF")
    parser.add_argument("--batch-size", type=int, default=50, help="每批结构数量，不是总下载上限（1—500）")
    parser.add_argument("--max-systems", type=int, default=4095, help="子体系枚举数量上限，超出会提示而不会截断结果")
    parser.add_argument("--retries", type=int, default=3, help="请求失败时的总尝试次数（1—5）")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = make_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 500 or not 1 <= args.retries <= 5 or args.max_systems < 1:
        parser.error("batch-size 必须为 1—500，retries 必须为 1—5，max-systems 必须为正整数。")
    api_key = ""
    try:
        if not args.elements:
            if not sys.stdin.isatty():
                raise ValueError("非交互运行时请在命令后输入元素，例如 python download_cif.py Li Fe O。")
            args.elements = [input("请输入元素符号，用空格分开（例如 Li Fe O）： ")]
        elements = parse_elements(args.elements)
        build_criteria(elements, args.mode, args.include_deprecated, args.stable_only, args.max_systems)
        if sys.version_info < (3, 11):
            raise RuntimeError("需要 Python 3.11 或更新版本。建议单独建立 mp-cif 环境，不改动 NepTrain/GPUMD 环境。")
        try:
            from mp_api.client import MPRester
            from pymatgen.core import Structure
            from pymatgen.io.cif import CifWriter
        except Exception as exc:
            raise RuntimeError("依赖没有安装或导入失败。请在独立环境运行 python -m pip install -r requirements.txt。\n依赖错误：" + str(exc)) from None
        api_key = os.environ.get("MP_API_KEY", "").strip()
        if not api_key:
            if not sys.stdin.isatty():
                raise ValueError("非交互运行需要先设置环境变量 MP_API_KEY。")
            api_key = getpass.getpass("请输入 Materials Project API Key（不显示，也不保存）： ").strip()
        if not api_key or re.search(r"\s", api_key):
            raise ValueError("API Key 不能为空或包含空白字符。请从 Materials Project 账号页面复制完整 Key。")
        folder = (args.output or Path.cwd() / f"MP_CIF_{'-'.join(elements)}_{args.mode}").expanduser().resolve()
        query = {"elements": elements, "mode": args.mode, "include_deprecated": args.include_deprecated, "stable_only": args.stable_only}
        print(f"工具版本：{VERSION}\n元素：{' '.join(elements)}\n范围：{MODE_NAMES[args.mode]}\n输出：{folder}", flush=True)
        print("每个材料编号导出一个 CIF，默认包括稳定及不稳定结构；不下载全部历史计算。", flush=True)
        with MPRester(api_key=api_key, use_document_model=False, mute_progress_bars=True) as mpr:
            return download(mpr.materials.summary, folder, query, args, api_key, Structure, CifWriter)
    except KeyboardInterrupt:
        print("\n已中断；成功保存的文件和清单保留，再运行相同命令可续传。", file=sys.stderr)
        return 130
    except (Exception, EOFError) as exc:
        print("错误：" + clean_error(exc, api_key), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
