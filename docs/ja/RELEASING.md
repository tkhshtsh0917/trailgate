# リリース確認項目

[English](../RELEASING.md)

commit、push、tag、GitHub release、package index での公開は別々の操作です。tag を作る前に候補を review し、以下の項目を完了してください。package の公開は任意の別作業です。

リリース用の build には uv 0.12.10 を使用します。CI と、検査対象の wheel generator metadata に合わせるためです。

## 候補の検査

1. クリーンな checkout で `uv sync --locked` を実行し、[架空データの一通りの手順](GUIDE.md)を試してください。model key や Langfuse server は不要です。
2. `uv run --locked ruff check src tests examples scripts`、`uv run --locked ruff format --check src tests examples scripts`、`uv run --locked python -m unittest discover -s tests -q` を実行します。unit test suite は `--extra bulk-parquet` を付けて再実行します。
3. `uv run --locked python scripts/benchmark_bulk.py --rows 100000 --max-rss-mib 1024` を実行し、`--observations-per-trace 1` を付けて再実行します。machine、最大 RSS、経過時間を記録してください。gate は通常と高 trace cardinality での取り込みと概要出力を対象とし、大規模な graph export は対象外です。
4. `uv build` で新しい wheel と sdist を作ります。`uv run --locked python scripts/check_release_artifacts.py` でソースの bytes、metadata、同梱 JSON Schema、正確な archive member の許可リストを照合します。新しい環境への wheel install と `uv run --locked python scripts/check_public_files.py` も確認します。別のクリーンなディレクトリで build を繰り返し、SHA-256 digest を比較して、同じ platform と toolchain での再現性を確認します。
5. `uv run --locked python scripts/check_dependency_safety.py` と `uv audit --locked` を実行します。前者は offline の Brotli version 下限検査です。後者は OSV への network access が必要で、lock されたすべての extra と group を対象にします。監査 service を利用できない場合は「未検証」と扱い、「問題なし」としてはいけません。
6. `git status --short --untracked-files=all` と `git diff --cached --name-only` を確認します。environment file、credential、非公開 capture/archive/dataset、実際の corpus、machine 固有の path は stage しないでください。repository ローカルの Git identity を確認し、検証結果を超える主張がないか文書を読み直してください。
7. [v1 の契約と移行](CONTRACT.md)、[脅威モデル](SECURITY.md)、[比較の範囲](COMPARISON.md)を確認します。宣言した Python version で CI が通る必要があります。compatibility matrix にない Langfuse または Parquet の組み合わせは、未検証と明記してください。

release artifact checker は、正確な member 集合、CLI entry point、project metadata、wheel の RECORD、検査されていない ZIP/gzip/tar metadata、sdist の有効な build 設定を検証します。公開ファイル scanner は heuristic です。機微情報を示唆するファイル名を内容を読む前に拒否し、text の検出事項は rule 名と行番号だけを報告します。すべての非公開情報を検出できる保証はありません。

## review 後

利用者による review と許可を得た後に限り、`v1.0.0` tag を作成・push します。GitHub release や package index での公開は別途判断します。任意の live 実行結果を release artifact に含めないでください。
