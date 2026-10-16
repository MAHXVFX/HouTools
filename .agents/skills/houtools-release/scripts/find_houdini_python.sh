#!/usr/bin/env bash
# Print the path of Houdini 22's bundled python313 interpreter (any drive).
# Resolution order: $HFS (the running Houdini itself) -> installed
# "Houdini 22.*" dirs on every drive, highest version first (sort -V).
# Used by the houtools-release skill so no absolute install path is hardcoded;
# run from Git Bash (ZCode's shell). Exit 1 with a hint when not found.

set -u

dirs=()
[ -n "${HFS:-}" ] && dirs+=("$HFS")
# Git Bash 根目录不含盘符条目（/d 由路径层翻译，ls / 看不到 d），
# 无法用 /? 通配枚举磁盘，只能显式遍历盘符字母
for letter in {c..z}; do
    base="/$letter/Program Files/Side Effects Software"
    [ -d "$base" ] || continue
    for d in "$base"/Houdini\ 22.*; do
        [ -d "$d" ] && dirs+=("$d")
    done
done

# $HFS 之外的候选按版本从高到低排：glob 字典序不等于数值序
# （"22.0.8" 会排在 "22.0.99" 前面），直接取第一个命中会选到低版本
if [ ${#dirs[@]} -gt 1 ]; then
    hfs_first="${dirs[0]}"
    rest=("${dirs[@]:1}")
    mapfile -t rest < <(printf '%s\n' "${rest[@]}" | sort -Vr)
    dirs=("$hfs_first" "${rest[@]}")
fi

for d in "${dirs[@]}"; do
    if [ -x "$d/python313/python.exe" ]; then
        echo "$d/python313/python.exe"
        exit 0
    fi
done

echo "Houdini 22 python313 not found (set \$HFS, or install under 'Program Files/Side Effects Software')" >&2
exit 1
