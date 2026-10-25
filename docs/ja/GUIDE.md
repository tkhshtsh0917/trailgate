# 架空データを使う一通りの手順

[English](../GUIDE.md)

この手順では付属の架空データだけを使います。network request もモデル呼び出しも行いません。Python 3.12.13 と `uv` を用意し、新しい checkout のルートから実行してください。

```sh
uv sync --locked
uv run --locked trailgate doctor --source langfuse --input examples/synthetic/snapshot
uv run --locked trailgate ingest langfuse-api-snapshot examples/synthetic/snapshot --out private-example
uv run --locked trailgate report private-example
uv run --locked trailgate inspect private-example --trace trace-example
uv run --locked trailgate export graph private-example --out private-graph.json
uv run --locked trailgate evaluate dataset private-example \
  --rights-file examples/synthetic/rights.json \
  --policy-file examples/synthetic/policy.json --use internal_training
uv run --locked trailgate export dataset private-example \
  --rights-file examples/synthetic/rights.json \
  --policy-file examples/synthetic/policy.json --use internal_training \
  --out private-dataset
```

**変更していない** fixture から、適格な Chat SFT 行が2件、診断用 episode 行が1件得られます。`private-dataset/manifest.json` でソース hash、出力 hash、件数、用途、権利申告、policy hash、各 view のラベルを確認してください。`private-dataset/chat_sft.jsonl` は gate を通った Chat view です。`episodes.jsonl` は診断用であり、暗黙に学習データとして扱ってはいけません。同じ bytes の archive と申告ファイルを用い、別の空の出力先へ繰り返し export すると、出力ファイルは一致するはずです。

## 自分の Langfuse project から始める

1. `CaptureWriter` を使い、アプリのモデル呼び出し境界を計装して、各 capture ID を対応する generation に付けます。モデルへ実際に渡された tool 結果も記録します。gate を通る Chat SFT には、trace 内の全 observation で一貫した session ID が必要です。[capture](CAPTURE.md)を参照してください。
2. ソース経路を一つ選びます。保存済み API page、範囲を限定した読み取り専用の [pull](PULL.md)、または完全なローカルの [enriched blob run](BLOB.md) です。元のソースと取り込んだ archive は非公開にしてください。capture のない bulk run も診断には使えますが、忠実な Chat SFT decision は得られません。
3. `report` と `inspect` で capture の欠落、曖昧な link、省略を確認します。完全な context が必要な場合に限り、`export graph` を非公開の場所へ出力してください。score は後から付く根拠として扱います。
4. archive の正確な `snapshot_id` に対応する新しい rights と policy の申告を作り、架空データ用のファイルを使い回さないでください。generation ID、回答品質、機微情報、task 単位の split を確認します。`evaluate dataset` で事前確認し、新しい非公開ディレクトリへ export してください。[dataset gate](DATASET.md)を参照してください。
5. 下流の学習を行う前に、manifest と選択された行を確認します。Trailgate は upload、token 化、モデル学習を行いません。

## 失敗例

| 表示 | 意味と次の操作 |
|---|---|
| `snapshot has an unsaved next page` | 保存済み API page に次の cursor が残っています。残りの page を取得するか、元の範囲付き pull を再開してください。手作業で `complete: true` に変えてはいけません。 |
| `raw archive hash mismatch` | 取り込み後に元の bytes が変わっています。元の snapshot を復元するか、新しい archive を作成してください。hash を修正してはいけません。 |
| `rights snapshot_id does not match archive` | 権利申告が別の snapshot に属しています。この snapshot を確認して新しい申告を作成してください。 |
| `gate:quality_manual_selection` | generation が手動承認されていません。Langfuse の score だけでは承認できません。 |
| `structural:unmatched_tool_result` | tool call に対して、capture された prefix 内で検証済みの返却結果がありません。アプリの計装を確認し、行は診断用として残してください。 |
| `archive exceeds the in-memory view limit` | 取り込み自体は有効ですが、graph・audit・dataset のメモリ上での構築には大きすぎます。逐次処理の `report`/`inspect` を使い、収集時間範囲を小さくしてください。 |

出力と capture には非公開の prompt や tool データが含まれ得ます。CLI の通常 report は内容を表示しませんが、`export graph`、`export sft`、`export dataset` は非公開ファイルへ書き込みます。残る責任は[脅威モデル](SECURITY.md)を参照してください。
