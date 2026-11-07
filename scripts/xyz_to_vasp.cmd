@echo off
rem = """
setlocal DisableDelayedExpansion
set "NEPKIT_PYTHON=D:\software\anaconda\envs\nepkit\python.exe"
if not exist "%NEPKIT_PYTHON%" (
    echo NepKit Python was not found at "%NEPKIT_PYTHON%".
    pause
    exit /b 1
)
"%NEPKIT_PYTHON%" -x "%~f0" %*
set "CONVERT_STATUS=%errorlevel%"
if "%~1"=="" pause
if not "%~1"=="" if not "%CONVERT_STATUS%"=="0" pause
exit /b %CONVERT_STATUS%
"""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from ase.io import iread, write


def choose_xyz():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilename(
            parent=root,
            title="选择要转换的 XYZ 文件",
            filetypes=[("XYZ / Extended XYZ", "*.xyz *.extxyz"), ("所有文件", "*.*")],
        )
    finally:
        root.destroy()


def convert(source):
    if not source.is_file():
        raise ValueError(f"找不到输入文件 {source}")

    # Only publish the output folder after every frame has been converted.
    with TemporaryDirectory(prefix=".xyz_to_vasp_", dir=source.parent) as temporary:
        staging = Path(temporary) / "vasp"
        staging.mkdir()
        count = 0
        nonperiodic = False
        with source.open(encoding="utf-8-sig") as handle:
            for count, atoms in enumerate(iread(handle, index=":", format="extxyz"), 1):
                cell = atoms.cell.array
                if not np.isfinite(cell).all() or np.linalg.matrix_rank(cell) != 3:
                    raise ValueError(f"第 {count} 帧缺少有效晶胞。请先补好晶胞，再导出含 Lattice 的 Extended XYZ。")
                if not len(atoms) or not np.isfinite(atoms.positions).all():
                    raise ValueError(f"第 {count} 帧缺少有效的原子坐标。")
                nonperiodic = nonperiodic or not atoms.pbc.all()

                # Group each species once, keeping the order in which species first appear.
                species = {}
                for index, symbol in enumerate(atoms.get_chemical_symbols()):
                    species.setdefault(symbol, []).append(index)
                ordered = atoms[[index for indices in species.values() for index in indices]]
                ordered.set_array("momenta", None)
                output = staging / f"{source.stem}_{count:04d}.vasp"
                write(output, ordered, format="vasp", direct=False, sort=False, vasp5=True)

        if not count:
            raise ValueError("输入文件里没有读到结构。")

        destination = source.with_name(f"{source.stem}_vasp")
        suffix = 2
        while True:
            try:
                staging.rename(destination)
                break
            except FileExistsError:
                destination = source.with_name(f"{source.stem}_vasp_{suffix}")
                suffix += 1
        return count, destination, nonperiodic


def main():
    parser = argparse.ArgumentParser(description="逐帧把 Extended XYZ 转成 VASP POSCAR。")
    parser.add_argument("xyz", nargs="?", help="输入文件。省略时打开文件选择窗口。")
    args = parser.parse_args()
    try:
        filename = args.xyz or choose_xyz()
        if not filename:
            return 0
        count, destination, nonperiodic = convert(Path(filename).expanduser().resolve())
    except Exception as error:
        print(f"转换失败：{error}")
        return 1

    print(f"已导出 {count} 个 .vasp 文件。")
    print(f"输出文件夹：{destination}")
    if nonperiodic:
        print("源文件含非周期方向。POSCAR 按三维周期结构读取，请核对晶胞和真空间隔。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
