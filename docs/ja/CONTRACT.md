# v1 の契約と移行

[English](../CONTRACT.md)

Trailgate 1.0.0 が維持するのは、**文書化された** CLI と Python の入口です。`_` で始まる内部関数は対象外です。文書化された契約の削除・変更は major version で行います。minor version では、既存 field の意味を変えずにコマンド、field、schema を追加できます。patch version は不具合の修正です。利用側は未知の object field を無視し、未知の `schema_version` は拒否してください。capture にない episode 境界や model context を推測してはいけません。

## 対応する interface

| interface | v1 の契約 |
|---|---|
| CLI | 文書化された option とともに `doctor`、`ingest`、`pull`、`ingest-blob`、`capture-recover`、`report`、`inspect`、`evaluate dataset`、`export steps/graph/sft/dataset` を提供します。成功時の終了 code は `0`、検証または I/O の失敗時は `2` です。通常の診断は capture 内容を表示しません。`export sft` は明示的に候補診断用のままです。 |
| Python | `trailgate.capture.CaptureWriter`、`trailgate.snapshot.{read_snapshot,ingest_snapshot,SnapshotError}`、`trailgate.archive.{read_archive,iter_archive_rows}`、`trailgate.pull.{PullSpec,preview_pull,pull_snapshot}`、`trailgate.bulk_archive.ingest_blob_export`、`trailgate.episode_graph.build_episode_graph`、`trailgate.dataset_assessment.evaluate_dataset`、`trailgate.dataset_export.export_dataset`、`trailgate.schemas.{SCHEMA_FILES,load_schema}`。guide で示す呼び出しには keyword argument を使ってください。 |
| ファイル | 保存済み API snapshot v1、capture v1、archive v1/v2、episode graph v1、use manifest v1、dataset policy v1、dataset manifest v1、Chat SFT v1、診断用 episode row v1。[schema](#公開-json-schema)を参照してください。 |

`read_archive` と graph・audit・dataset の構築は、上限のあるメモリ上の view です。`iter_archive_rows`、bulk の `report`、`inspect` は、検証済みのより大きな archive を扱えます。上限と除外条件は v1 の契約に含まれます。実装は minor release で上限を引き上げる場合があります。出力先は新しい path である必要があります。`export graph`、候補 SFT、dataset file には非公開の内容が含まれ得ます。

通常の CLI 診断は、ソースに含まれる任意の未知 field 名を表示せず、その件数を報告します。非公開 archive は出典のために元の field 名を保持します。

## 公開 JSON Schema

Draft 2020-12 の JSON Schema 9件を wheel と sdist に同梱します。`from trailgate.schemas import SCHEMA_FILES, load_schema` を使い、例えば `load_schema("dataset-manifest-v1.json")` で読み込めます。checkout 内では `src/trailgate/schemas/` からも読めます。実行時に JSON Schema の依存 library は不要です。project のテストで行う schema 検証には開発用の依存 library を使います。

schema 検証は、必須の外枠と一部 field の型を確認します。Trailgate の Python validator はさらに、ファイル間の hash、cursor の終端、ID の一意性、時間範囲、link、split の一貫性、利用目的の申告を確認します。schema-valid なファイルでも、完全性、品質、プライバシー、法的権利、適格性は**証明されません**。従来の架空 fixture には version のない capture event が含まれます。importer はこれを受け付けますが、公開する capture v1 schema が記述するのは `CaptureWriter` が書き出す event です。

## v0.2.0 からの移行

破壊的な archive の書き換えは不要です。既存の `trailgate.archive.v1` API archive と `trailgate.archive.v2` blob archive は、元のファイルと hash を検証できれば読み込めます。元のソース snapshot または複製した blob run を保持してください。将来 adapter が変わった場合の復旧元になります。dataset を作り直すには、archive の `snapshot_id` に紐づく rights・policy 申告を使い、`evaluate dataset` と `export dataset` を**新しい**出力ディレクトリへ再実行します。下流の参照先を変更する前に新しい manifest と件数を確認してください。archive index、元の bytes、その hash field を直接書き換えてはいけません。

v0.2 の `export sft` 出力は候補診断用のままであり、v1 の gate 済み Chat SFT view として表示し直してはいけません。`trailgate.chat_sft.v1` を期待する利用側は、`export dataset` の `chat_sft.jsonl` を読んでください。`episodes.jsonl` は常に診断用です。アプリの sidecar と Langfuse の event は不完全な場合があります。importer は欠落を補わず、不確実性として記録します。互換性のない file 変更には新しい schema version を導入します。将来の reader は、その version を明示的に受け付けるか、非対応 version であることを明瞭に伝えて失敗します。

## 確認済みの互換性

| 対象 | ローカルで確認したこと | 契約と制限 |
|---|---|---|
| Python | CPython 3.12.13、3.13.15、3.14.7 での新しい wheel のインストール、架空データの CLI smoke test、ローカルの unit test suite | 宣言した範囲は `>=3.12.13,<3.15` で、CI は3 version すべてでテストします。core は標準 library のみを使用します。任意の Parquet 対応には `pyarrow>=26,<27` が必要で、CI は 3.12.13 で実行します。 |
| self-hosted Langfuse | 4.56.0 と 4.70.0 で、Observations v2 / Scores v3、架空の trace と score | REST の読み取り経路と offline import の一致を、モデル呼び出しなしで両 server に対して確認しました。同じ endpoint を持つその他の server version は live test 未実施です。endpoint の可用性は [Langfuse の version 文書](https://langfuse.com/self-hosting/upgrade/versioning)を参照してください。 |
| Langfuse Python SDK | v1.0.0 tag の任意のローカル診断では 4.18.0 を使用しました。現在の `main` は 5.0.4 を lock し、認証情報・モデル呼び出しなしで Scores v3 の accessor を確認しました。 | core collector は REST を使い、SDK に依存しません。任意の live helper は v4 と v5 の score 読み取り accessor を選択します。SDK v5 と Langfuse server 間の end-to-end 実行は未確認です。SDK v5 を self-hosted で使うには Langfuse v4.59.0 以降が必要です。[v5 移行 guide](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5)を参照してください。 |
| Enriched blob export | Manifest v1、`EVENTS`、`observations_v2` と `scores`、JSONL・gzip JSONL・Parquet の架空 fixture | offline の conformance、resume、resource test は通過しました。実運用の Langfuse blob export run は未検証です。[bulk 取り込み](BLOB.md)を参照してください。 |

この表は確認できた範囲を示すものであり、将来のすべての Langfuse version の動作を約束しません。新たな server または SDK の組み合わせは、同じ架空データの integration fixture で検証してから追加してください。
