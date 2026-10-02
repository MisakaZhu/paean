# 识别已有成片的配乐

```bash
python SCRIPT_DIR/identify_music.py ./finished.mp4 --source ./source.mp4 --music ./music
```

默认仅比对使用者曲库；原片未指定时，只在配置的 `source_root` 和本机工作区日志中寻找同标题候选。名称匹配本身不证明原片身份，使用者或 agent 仍需核对内容。

只有实际使用者允许时，才增加：

```bash
python SCRIPT_DIR/identify_music.py ./finished.mp4 --cache-dir ./authorized-cache --drafts-dir ./authorized-drafts
```

指定缓存/草稿路径应准确到本次允许的目录；不自动寻找应用配置、其他用户资料或整个磁盘。草稿只读取与本次标题相关的音乐字段，不修改任何草稿或缓存。

脚本使用相关性给出候选曲目，用匹配原片时的最小二乘估计人声/BGM 增益和响度差。压缩、混音、循环、淡化和来源不一致会影响估计。分数接近、片段过短或匹配不清楚时保留不确定性，不能把第一名当作确认结果。

先查看候选并核对音频及使用权，再需要保存该候选时使用：

```bash
python SCRIPT_DIR/identify_music.py ./finished.mp4 --cache-dir ./authorized-cache --save 我的曲目 --confirm-match
```

`--confirm-match` 只表示调用者已完成核对，不是脚本验证了版权或匹配。保存不覆盖既有曲库文件；源缓存保持原样。音乐已有曲库版本时直接使用它，不重复复制。
