# ローカルの enriched blob export を取り込む

[English](../BLOB.md)

Langfuse の blob export の完了した一回分を、単一のローカル object root に複製します。各 object の**完全な key**を相対パスとして保持し、その run の manifest を `--manifest` に指定してください。

```sh
uv run --locked trailgate ingest-blob \
  --object-root private-objects \
  --manifest private-objects/PROJECT_ID/manifests/RUN.json \
  --out private-bulk-archive
uv run --locked trailgate report private-bulk-archive
```

Trailgate は manifest の `files[].key` を使用し、テーブルのファイル名を推測しません。現在対応するのは、`exportSource: EVENTS` で `observations_v2` と `scores` の両方を含む manifest v1 です。形式は JSONL、gzip JSONL、Parquet に対応します。CSV、JSON 配列、旧形式の observations/traces、複数ソースを含む manifest は明示的に失敗します。Parquet は任意機能で、`uv sync --locked --extra bulk-parquet` により PyArrow を導入できます。完了状態、field group、形式の違いは [Langfuse の blob export 文書](https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage)を参照してください。

参照される object はすべて root 内に存在し、宣言された `sizeBytes` と一致する必要があります。テキストファイルは `rowCount` も一致する必要があります。元の bytes と SHA-256 hash を保持します。正規化では非公開の SQLite spool を使って逐次処理し、時刻と ID で出力を並べます。JSONL の一行はデコード後 16,777,216 文字までです。同じ内容の重複 ID はまとめ、内容が異なる重複 ID は失敗させます。途中で停止した場合は、同じ manifest と出力パスで `--resume` を指定して再実行してください。入力が変わらなければ正規化後の hash も同じになります。spool の確定後から archive の rename 前までを含め、manifest や object が変われば失敗します。モデル呼び出し境界の capture がない bulk archive からは構造の概要のみ得られ、忠実な学習候補は得られません。

架空データの 100,000 observation benchmark は `uv run --locked python scripts/benchmark_bulk.py --rows 100000` で再実行できます。異なる trace 100,000 件を測るには `--observations-per-trace 1` を追加します。計測対象は取り込みと概要出力の最大プロセス RSS および経過時間であり、巨大な個別 payload やリモート object store の転送は対象外です。bulk の概要と `inspect` は検証済み行を逐次処理します。正確な bulk trace 件数には一時的な非公開 SQLite index を使うため、異なる trace の件数に応じたディスク容量が必要です。通常終了時にはこの index を削除します。メモリ上の graph、audit、dataset view は、archive が observation 20,000 件または正規化行と capture の合計 32 MiB を超えると停止します。その場合はソースの時間範囲を小さくしてください。元の Parquet が正本です。型付き timestamp や decimal 値を JSON に正規化すると表示形式が変わり得るため、厳密な数値表現が必要な場合は元の行を確認してください。
