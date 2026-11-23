from pathlib import Path
p=Path('D:/shukong/review-app/tests/test_core.py');s=p.read_text(encoding='utf-8')
a=s.index('    def test_vision_disabled_is_explicit');b=s.index('    def test_review_export_contains_reason',a)
s=s[:a]+'''    def test_missing_cli_is_explicit(self):
        with patch.object(server,'codex_binary',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'Codex CLI'):server.vision([], [5])

    def test_vision_output_constraints(self):
        response={'issues':[
            {'page':1,'rule':5,'title':'定位需复核','reason':'测试接口返回','box':[.1,.2,.4,.5]},
            {'page':1,'rule':99,'box':[.1,.2,.4,.5]},
            {'page':1,'rule':5,'box':[-1,0,1,1]},
            {'page':1,'rule':5,'box':[.8,.2,.1,.4]},
            {'page':3,'rule':5,'box':[.1,.2,.4,.5]}
        ]}
        def execute(args,**kwargs):
            Path=pathlib.Path
            Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(response),encoding='utf-8')
            return server.subprocess.CompletedProcess(args,0,b'{}',b'')
        d=fitz.open();d.new_page()
        with patch.object(server,'codex_binary',return_value='codex.exe'),patch.object(server.subprocess,'run',side_effect=execute) as mock:
            issues,status=server.vision(d,[5])
        self.assertEqual(len(issues),1);self.assertEqual(issues[0]['severity'],'warning')
        self.assertIn('--image',mock.call_args.args[0]);self.assertIn('--output-schema',mock.call_args.args[0])
        self.assertIn(b'1',mock.call_args.kwargs['input'])
        d.close()

    def test_cad_copies_are_unique_and_ignore_locks(self):
        with tempfile.TemporaryDirectory() as t:
            base=pathlib.Path(t);source=base/'source';source.mkdir()
            for name in ['a.SLDDRW','a.SLDPRT','~$a.SLDPRT']:(source/name).write_bytes(b'cad')
            result=server.subprocess.CompletedProcess([],0,b'',b'')
            with patch.object(server.subprocess,'run',return_value=result):
                mapped=server.prepare_cad(base,source,'unique-task-123456')
            self.assertEqual(len(mapped),2)
            self.assertTrue(all('__unique-task-' in p.name for p in mapped.values()))
            self.assertEqual((source/'a.SLDDRW').read_bytes(),b'cad')

'''+s[b:]
s=s.replace("with patch.object(server,'DATA',root),patch.dict(server.os.environ,{'VLLM_BASE_URL':'','VLLM_MODEL':''}):", "with patch.object(server,'DATA',root),patch.object(server,'vision',return_value=([],'mock CLI')):")
p.write_text(s,encoding='utf-8')
# Update documentation to match the actual provider.
p=Path('D:/shukong/review-app/README.md');s=p.read_text(encoding='utf-8');a=s.index('## 视觉模型配置');b=s.index('## 规则覆盖',a)
s=s[:a]+'''## Codex CLI 视觉审核

默认通过本机已登录的 `codex exec` 审核 PDF 图像，无需单独配置 VLLM 服务。程序自动查找 PATH 或 Codex 桌面版安装目录的 `codex.exe`。

- 使用 `--image` 传入同一图纸的全部页面，`--output-schema` 约束问题JSON，`--output-last-message`保存结果。
- 只读、临时会话；不修改用户Codex配置、不切换账号。默认使用CLI现有模型；可用 `CODEX_VISION_MODEL` 指定模型，`CODEX_CLI_PATH` 指定可执行文件。
- 逐页图片、审核提示、CLI日志与结构化结果保存在任务目录 `vision/<文件ID>/`。
- 模型意见按待复核展示。执行失败会显示明确原因，不自动判为合格。
- 已在零件39上实测图片输入和结构化审核输出。Codex CLI使用当前登录账号对应的模型服务，图纸图片会随审核请求提交给该服务。

## 同名工程图与失败重试

SolidWorks错误65536表示已有同名文档打开。程序在每批任务下创建唯一名称的CAD工作副本，并通过SolidWorks API重定向包内模型引用，保留原ZIP和解压原件，不关闭用户已有文档。忽略 `~$` 锁文件。缺失的外部/历史引用会写入 `cad-prepare.log`，相关几何完整性需复核；模型同名歧义则停止准备。

点击“重试失败图纸”可仅重试失败的文件，成功的PDF保留。补齐转换后回到待确认，需再次确认后审图。已完成任务可以点击“重新审图”，使用当前规则与Codex CLI重新审核。

'''+s[b:]
s=s.replace('模型拓扑/约束求解器未实现。','模型拓扑/约束求解器未实现。').replace('视觉响应坐标与规则约束校验','Codex CLI响应坐标与规则约束校验')
p.write_text(s,encoding='utf-8')
