"""関連する生成ファイルを準備してから置換する。"""

import os
import shutil
from pathlib import Path
from tempfile import mkdtemp


def publish(base: Path, files: dict[str, bytes]) -> dict[str, str]:
    """例外時は旧版へ戻す。強制終了・並行実行は対象外。"""
    base.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=f".{base.name}-", dir=base.parent))
    paths = {suffix: Path(str(base) + suffix) for suffix in files}
    backups = {}
    replaced = []
    recovery = False
    try:
        for i, (suffix, content) in enumerate(files.items()):
            (staging / f"new-{i}").write_bytes(content)
            if paths[suffix].exists():
                backup = staging / f"old-{i}"
                shutil.copy2(paths[suffix], backup)
                backups[suffix] = backup
        for i, (suffix, path) in enumerate(paths.items()):
            os.replace(staging / f"new-{i}", path)
            replaced.append(suffix)
    except BaseException as error:
        failures = []
        for suffix in reversed(replaced):
            try:
                if suffix in backups:
                    os.replace(backups[suffix], paths[suffix])
                else:
                    paths[suffix].unlink()
            except OSError:
                failures.append(str(paths[suffix]))
        if failures:
            recovery = True
            raise OSError(f"復元失敗: {failures}; 旧版退避先: {staging}") from error
        raise
    finally:
        if not recovery:
            shutil.rmtree(staging)
    return {suffix: str(path) for suffix, path in paths.items()}
