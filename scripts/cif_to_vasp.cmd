@echo off
rem = """
setlocal DisableDelayedExpansion
set "CIF_PYTHON=D:\software\anaconda\python.exe"
if not exist "%CIF_PYTHON%" (
    echo Anaconda Python was not found at "%CIF_PYTHON%".
    pause
    exit /b 1
)
"%CIF_PYTHON%" -x "%~f0" %*
set "CONVERT_STATUS=%errorlevel%"
if "%~1"=="" pause
if not "%~1"=="" if not "%CONVERT_STATUS%"=="0" pause
exit /b %CONVERT_STATUS%
"""

import argparse
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory

from pymatgen.io.cif import CifParser, str2float
from pymatgen.io.vasp import Poscar


def choose_cifs():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilenames(
            parent=root,
            title="选择要转换的 CIF 文件（可多选）",
            filetypes=[("CIF", "*.cif"), ("所有文件", "*.*")],
        )
    finally:
        root.destroy()


def read_poscars(source):
    parser = CifParser(source, frac_tolerance=0)
    for block, data in parser.as_dict().items():
        occupancies = data.get("_atom_site_occupancy", [])
        if isinstance(occupancies, str):
            occupancies = [occupancies]
        for occupancy in occupancies:
            if occupancy in ("?", ".") or not isclose(
                str2float(occupancy), 1.0, rel_tol=0, abs_tol=1e-8
            ):
                raise ValueError(f"数据块 {block} 有部分或未知占位（{occupancy}），请先确定原子占位。")

    structures = parser.parse_structures(primitive=False, on_error="raise")
    outputs = []
    for index, structure in enumerate(structures, 1):
        if not structure.is_ordered:
            raise ValueError(f"第 {index} 个结构含混合或部分占位，请先确定原子占位。")
        # POSCAR groups atoms by element. Keep each site's element and coordinate paired.
        elements = list(dict.fromkeys(site.specie.symbol for site in structure))
        ordered = structure.get_sorted_structure(key=lambda site: elements.index(site.specie.symbol))
        text = Poscar(ordered).get_str()
        suffix = f"_block_{index:04d}" if len(structures) > 1 else ""
        outputs.append((f"{source.stem}{suffix}", text))
    return outputs


def convert(sources):
    parent = sources[0].parent
    if any(source.parent != parent for source in sources):
        raise ValueError("一次请选择同一个文件夹里的 CIF 文件。")

    count = 0
    failures = 0
    folder_name = f"{sources[0].stem}_vasp" if len(sources) == 1 else "cif_vasp"
    with TemporaryDirectory(prefix=".cif_to_vasp_", dir=parent) as temporary:
        staging = Path(temporary) / "vasp"
        staging.mkdir()
        for source in sources:
            try:
                outputs = read_poscars(source)
            except Exception as error:
                print(f"跳过 {source.name}：{error}")
                failures += 1
                continue
            for stem, text in outputs:
                output = staging / f"{stem}.vasp"
                suffix = 2
                while output.exists():
                    output = staging / f"{stem}_{suffix}.vasp"
                    suffix += 1
                with output.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                count += 1

        if count == 0:
            return failures
        destination = parent / folder_name
        suffix = 2
        while True:
            try:
                staging.rename(destination)
                break
            except FileExistsError:
                destination = parent / f"{folder_name}_{suffix}"
                suffix += 1

    print(f"已导出 {count} 个 .vasp 文件。")
    print(f"输出文件夹：{destination}")
    return failures


def main():
    parser = argparse.ArgumentParser(description="把有明确原子占位的 CIF 转成 VASP POSCAR。")
    parser.add_argument("cif", nargs="*", help="一个或多个 CIF 文件。省略时打开文件选择窗口。")
    args = parser.parse_args()
    try:
        filenames = args.cif or choose_cifs()
        if not filenames:
            return 0
        sources = list(dict.fromkeys(Path(filename).expanduser().resolve() for filename in filenames))
        failures = convert(sources)
    except Exception as error:
        print(f"转换失败：{error}")
        return 1

    if failures:
        print(f"有 {failures} 个 CIF 未转换，请查看上面的原因。")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
