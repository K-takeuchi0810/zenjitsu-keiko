# 傾向収集

中央競馬の結果確定済みデータをSQLiteから読み、当日の傾向をMarkdown/CSVで出力します。

## 使い方

1. `run_latest.bat`
   - 結果確定済みの最新開催日を自動で集計します。
2. `run_today.bat`
   - 今日の日付で集計します。結果が未配信なら最新確定日の傾向に切り替え、今日の出馬表に対するおすすめ馬を出します。
   - 開催日の朝でもおすすめ馬がオッズ未取得で0件にならないよう、先にJV-Linkから当日の単複オッズを取得します（JV-Link不在時はスキップして集計のみ実行）。
3. `run_date.bat`
   - 指定した日付を集計します。
4. `sync_jvlink_then_collect.bat`
   - 既存の `keiba-yosou` でJRA-VAN RACEデータを取得、DBへ取り込みます。
   - 追い切り用にSLOP/WOOD（坂路・ウッド）も差分取得します。
   - 当日結果は速報系の `0B12` も取得するため、TARGETで結果が見えているタイミングなら当日の傾向まで集計できます。
   - 結果が未配信なら最新確定日の傾向へ切り替えます。
   - タスク実行時はRACE取込後に当日の開催番組を確認し、DBにない場合は追い切り・速報結果・オッズ取得とレポート生成をスキップします。
5. `run_weekly_validation_summary.bat`
   - 過去年ローリング検証（`trend_validation_<年>.csv`）の未登録ペアを処理し、`trend_validation_summary_<年>.md/csv` を再生成します。
   - 現行シーズンについても、検証CSVへ未登録の隣接開催ペアをすべて処理し、`trend_validation_summary.md/csv` を再生成します。
   - おすすめ馬ログへ実着順を反映し、`recommendation_validation_summary.md/csv` も再生成します。

出力先は `reports\YYYYMMDD` フォルダです。
スマートフォンで見る場合は、各日付フォルダの `mobile.html` または iCloud Drive 側の `index.html` を開いてください。

他の人へ共有する場合は、GitHub Pages で公開している次のURLを渡してください。20:10のバッチが `docs\index.html` を更新して自動でpushするため、常に最新レポートが表示されます。

```text
https://k-takeuchi0810.github.io/zenjitsu-keiko/
```

`collect_trends.py` は publish 時に最新HTMLを `docs\index.html` へも書き出します（`config.json` の `publish_to_docs` で無効化可）。git へのcommit/pushは `sync_jvlink_then_collect.bat` の末尾でのみ行い、`collect_trends.py` 自体はgitに触れません。
翌日の出馬表がDBにある場合は、当日傾向に合うおすすめ馬もHTML内に表示します。
傾向には枠・脚質・人気・上がり・血統を含みます。血統は馬場別にも表示します。
追い切りは `training_times` に対象馬の直近データが入っている場合、レース別に馬券内馬の最終追い切り種別と総時計を表示します。

出力ファイルが増えても見づらくならないよう、古い `YYYYMMDD_mobile.html` 形式のファイルは次回実行時に自動で日付別フォルダへ整理されます。

おすすめ馬は `reports\recommendation_log.csv` に記録されます。結果が確定した開催日を集計した時点、または週次検証バッチ実行時に、着順・勝ち・3着内が同じCSVへ反映されます。

## 設定

`config.json` で変更できます。

- `source_db`: 読み込む `keiba.db`
- `output_dir`: レポート出力先
- `publish_dir`: iCloud Drive へコピーする出力先
- `publish_to_icloud`: `true` なら生成後にiCloudへコピー
- `latest_min_races`: 最新開催日の判定に必要な最低レース数。24R未満は片場だけの部分取り込みと見なして自動判定では採用しません。
- `expected_races_by_date`: 特定日の期待レース数を上書きします。例: 3場開催で1場欠落を疑う日は `{ "20260606": 36 }` のように指定します。

最新開催日の自動判定では、DB上の開催場数から期待レース数を見積もります。同一週末の隣接日に3場分の番組が入っている場合、片日24Rだけのデータは不完全扱いにします。DBに両日とも1場分が丸ごと存在しない場合は外部情報なしには判定できないため、`expected_races_by_date` で補正してください。

## iCloudで見る

生成後、以下にもコピーされます。

```text
C:\Users\kizun\iCloudDrive\傾向収集\index.html
```

スマートフォン側ではiCloud Driveの `傾向収集/index.html` を開くと、最新のスマホ用レポートを確認できます。
過去分は `C:\Users\kizun\iCloudDrive\傾向収集\history\YYYYMMDD` に保存されます。

## テスト

回帰テストは次で実行します。小さなSQLite fixtureからCLI経由で `mobile.html` とおすすめ馬ログまで生成するE2Eテストも含みます。

```powershell
python -m unittest discover -s tests -v
```

## 自動実行

開催日を毎日20:00に確認して自動実行したい場合は、PowerShellで次を実行します。
当日の開催番組がDBになければ正常終了でスキップするため、祝日・代替開催も同じタスクで拾えます。

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
C:\Users\kizun\dev\傾向収集\create_weekend_task.ps1
```

傾向検証の集計を週次で更新したい場合は、PowerShellで次を実行します。月曜20:30（月曜20:00のデータ取込で日曜の確定結果が反映された後）に、検証CSVへ未登録の隣接開催ペアをすべて処理し、集計レポートを生成します。

```powershell
C:\Users\kizun\dev\傾向収集\create_weekly_validation_task.ps1
```

自動実行バッチの標準出力・エラー・終了コードは、次のフォルダに保存されます。

```text
C:\Users\kizun\dev\傾向収集\reports\logs
```

開催日確認・データ取得は `YYYYMMDD_HHMMSS_sync_jvlink_then_collect.log`、週次検証集計は `YYYYMMDD_HHMMSS_weekly_validation_summary.log` です。タスクが失敗した場合は、まず該当ログの末尾にある `Exit code` を確認してください。
