# 読み取り専用の Langfuse pull

[English](../PULL.md)

範囲を限定した時間間隔と、Langfuse API key に対応する project ID を指定してください。`--dry-run` は key を読まず server に接続せず、scope の検証と表示だけを行います。

```sh
uv run --locked trailgate pull langfuse \
  --base-url http://127.0.0.1:3000 --project-id YOUR_PROJECT_ID \
  --from-time 2026-10-01T00:00:00Z --to-time 2026-10-02T00:00:00Z \
  --out private-pull --dry-run
```

実際に pull するには、process environment に `LANGFUSE_PUBLIC_KEY` と `LANGFUSE_SECRET_KEY` を設定し、`--dry-run` を外してください。コマンドは [Observations API v2 と Scores API v3](https://langfuse.com/docs/api-and-data-platform/features/public-api) に対して GET request だけを送ります。平文 HTTP は loopback のみに許可し、周囲の HTTP proxy を無視し、redirect を拒否します。各 response は 32 MiB まで、429/5xx の retry は最大3回で、key や response body は表示しません。`--limit` は1〜100、`--max-pages` は**各** table の上限です。保存するすべての API page と capture sidecar は、メモリ上の snapshot 上限 64 MiB に収まる必要があります。より大きな収集には blob 取り込みを使ってください。`--trace-id` は収集範囲を絞り、scope の検証に必要な score subject を要求します。

各 response page は、その SHA-256 をファイル名に含めて `private-pull/pages/` に保存します。途中で停止したら、まったく同じコマンドに `--resume` を付けて再実行してください。同じ scope が必要であり、checkpoint の bytes が変わっていれば失敗します。両 table が null cursor に到達し、組み立てた snapshot の検証が通った後に、最終的な `manifest.json` を公開します。manifest には収集時刻と `cursor_exhausted_at_collection_time` を記録します。これは限定された API の走査についての記録であり、その時点ですべての trace や後から到着する score が存在したことの証明ではありません。

遅れて到着した score を照合するには、以前の **Trailgate pull** を取り込んだ archive を `--previous-archive` で指定し、**新しい**ディレクトリへ pull します。observation の時間範囲、project、trace、field group は一致する必要があります。score の時間範囲は同じ開始時刻から始め、`--score-to-time` で以前の `toTimestamp` より後まで伸ばせます。`--score-from-time` で別の score 開始時刻も設定できます。score 範囲全体を再収集すると、最初の範囲の後に作成された score と、最初の範囲内に遅れて現れた score の両方を取り込めます。新しい manifest は、別の score 時間範囲と前の snapshot ID を記録します。前の archive は変更しません。無関係な offline snapshot を前身だと申告することはできません。収集後、`trailgate ingest langfuse-api-snapshot private-pull --out private-archive` を実行します。

```sh
uv run --locked trailgate pull langfuse \
  --base-url http://127.0.0.1:3000 --project-id YOUR_PROJECT_ID \
  --from-time 2026-10-01T00:00:00Z --to-time 2026-10-02T00:00:00Z \
  --score-to-time 2026-10-04T00:00:00Z \
  --previous-archive private-archive --out private-pull-revision
```

resume 時、組み立て済み JSONL は保存された API page と byte 単位で一致する必要があります。不一致があれば調査のため checkpoint を未完了のまま残し、Trailgate は暗黙に置き換えません。

API の動作と可用性は Langfuse server の version に依存します。v2 observations endpoint には self-hosted Langfuse v4 が必要です。[公式の互換性文書](https://langfuse.com/self-hosting/upgrade/versioning)を確認してください。API cursor と利用者が指定した query 範囲だけでは、完全な episode 境界は確定しません。
