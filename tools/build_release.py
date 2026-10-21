# 배포 zip 만들기 (tools/build.py 를 먼저 실행)
#   python tools/build_release.py <버전> [출력 폴더(기본 dist/stage)]
#   → dist/MagicalGirlBaito_KoreanPatch_v<버전>.zip  (게임 폴더에 덮어쓸 파일 + OFL.txt)
import os, sys, zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ver = sys.argv[1]
    stage = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "dist", "stage")
    out = os.path.join(REPO, "dist", f"MagicalGirlBaito_KoreanPatch_v{ver}.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, _, fs in os.walk(stage):
            for fn in sorted(fs):
                p = os.path.join(root, fn)
                z.write(p, os.path.relpath(p, stage).replace(os.sep, "/"))
                n += 1
        z.write(os.path.join(REPO, "release", "OFL.txt"), "OFL.txt")
    print(f"{out}: {n + 1} files, {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
