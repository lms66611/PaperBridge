from __future__ import annotations
import json, os, pathlib, subprocess, sys, threading, tkinter as tk, tkinter.ttk as ttk
from tkinter import filedialog,messagebox
from .storage import get_api_key,set_api_key
from .usage import duration_label,job_usage

BG='#f4f6f7'; INK='#203139'; MUTED='#60727a'; ACCENT='#087f83'; WARNING='#ac563e'

class Application:
    def __init__(self,controller,bridge,hidden=False):
        self.controller,self.bridge=controller,bridge
        if hidden:print('UI smoke: tkinter root',flush=True)
        try:
            from tkinterdnd2 import TkinterDnD,DND_FILES
            self.root=TkinterDnD.Tk(); self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>',lambda e:self.add(self.root.tk.splitlist(e.data)))
        except Exception:
            # TkinterDnD can create a Tcl root and then fail while loading its
            # native extension. Dispose that root before making the fallback,
            # otherwise closing the visible window leaves mainloop running.
            leftover=getattr(tk,'_default_root',None)
            if leftover is not None:
                try:leftover.destroy()
                except tk.TclError:pass
            self.root=tk.Tk()
        if hidden:self.root.withdraw()
        if hidden:print('UI smoke: widgets',flush=True)
        root=self.root; root.title('PaperBridge · 文献对照翻译'); root.geometry('1080x700'); root.minsize(900,600)
        root.configure(bg=BG)
        style=ttk.Style(root); style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10),background=BG,foreground=INK)
        style.configure('TButton',padding=(11,7),borderwidth=1)
        style.configure('Accent.TButton',background=ACCENT,foreground='white')
        style.map('Accent.TButton',background=[('active','#096a6e'),('disabled','#cbd6d9')],
                  foreground=[('disabled','#6b7c81')])
        style.configure('Treeview',rowheight=40,background='white',fieldbackground='white',borderwidth=0)
        style.map('Treeview',background=[('selected','#dceeed')],foreground=[('selected',INK)])
        style.configure('Treeview.Heading',font=('Microsoft YaHei UI',10,'bold'),padding=(9,8),background='#e7edef')
        header=ttk.Frame(root,padding=(24,16,24,12));header.pack(fill='x')
        ttk.Label(header,text='PaperBridge',font=('Segoe UI',21,'bold')).pack(side='left')
        from . import __version__
        ttk.Label(header,text='v'+__version__,foreground=MUTED).pack(side='left',padx=(12,0),pady=(8,0))
        ttk.Button(header,text='设置',command=self.settings).pack(side='right')
        ttk.Button(header,text='Zotero 插件',command=self.link).pack(side='right',padx=7)
        ttk.Button(header,text='用量明细',command=self.usage_table).pack(side='right')
        ttk.Button(header,text='检查更新',command=lambda:self.check_updates(manual=True)).pack(side='right')
        ttk.Separator(root).pack(fill='x')

        toolbar=ttk.Frame(root,padding=(24,17,24,12));toolbar.pack(fill='x')
        ttk.Label(toolbar,text='文献任务',font=('Microsoft YaHei UI',14,'bold')).pack(side='left')
        self.summary=ttk.Label(toolbar,text='',foreground=MUTED)
        self.summary.pack(side='left',padx=(14,0),pady=(4,0))
        self.pause_button=ttk.Button(toolbar,text='暂停',command=self.pause)
        self.pause_button.pack(side='right')
        self.start_button=ttk.Button(toolbar,text='开始 / 继续',style='Accent.TButton',command=self.start)
        self.start_button.pack(side='right',padx=7)
        ttk.Button(toolbar,text='添加 PDF',command=lambda:self.add(filedialog.askopenfilenames(filetypes=[('PDF 文献','*.pdf')]))).pack(side='right')

        filters=ttk.Frame(root,padding=(24,0,24,10));filters.pack(fill='x')
        ttk.Label(filters,text='搜索',foreground=MUTED).pack(side='left',padx=(0,8))
        self.query=tk.StringVar()
        ttk.Entry(filters,textvariable=self.query,width=35).pack(side='left')
        ttk.Label(filters,text='状态',foreground=MUTED).pack(side='left',padx=(20,8))
        self.status_filter=tk.StringVar(value='全部')
        status=ttk.Combobox(filters,textvariable=self.status_filter,values=('全部','进行中','待处理','已完成'),
                            state='readonly',width=10)
        status.pack(side='left')

        frame=ttk.Frame(root,padding=(24,0,24,0));frame.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(frame,columns=('title','status','version','progress','elapsed'),show='headings',selectmode='extended',height=4)
        for key,text,width,minimum in [('title','文献',490,220),('status','状态',240,150),
                                       ('version','版本',70,60),('progress','进度',72,60),('elapsed','耗时',110,85)]:
            self.tree.heading(key,text=text);self.tree.column(key,width=width,minwidth=minimum,stretch=False,anchor='w')
        scrollbar=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview); self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left',fill='both',expand=True); scrollbar.pack(side='right',fill='y')
        self.tree.bind('<Configure>',lambda event:self.resize_columns(event.width))
        self.empty=tk.Label(self.tree,text='暂无文献任务',font=('Microsoft YaHei UI',11),bg='white',fg=MUTED)
        self.tree.bind('<Double-1>',lambda e:self.edit())
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.show_details())
        self.query.trace_add('write',lambda *_:self.refresh_once())
        self.status_filter.trace_add('write',lambda *_:self.refresh_once())

        self.detail=tk.StringVar(value='')
        self.detail_label=ttk.Label(root,textvariable=self.detail,foreground=MUTED,padding=(24,10,24,8))
        self.detail_label.pack(fill='x')
        self.detail_label.bind('<Configure>',lambda e:self.detail_label.configure(wraplength=max(300,e.width-48)))
        actions=ttk.Frame(root,padding=(24,2,24,15));actions.pack(fill='x')
        self.job_buttons={}
        for label,command in [('查看 PDF',self.open_result),('对照校对',self.edit),
                              ('重新导出 PDF',self.reexport),('附回 Zotero',self.import_selected)]:
            button=ttk.Button(actions,text=label,command=command)
            button.pack(side='left',padx=(0,7));self.job_buttons[label]=button
        ttk.Separator(root).pack(fill='x')
        footer=ttk.Frame(root,padding=(24,9,24,10));footer.pack(fill='x')
        self.connection=ttk.Label(footer,text='Zotero 未连接',foreground=MUTED)
        self.connection.pack(side='left')
        self.runtime_status=ttk.Label(footer,text='',foreground=MUTED)
        self.runtime_status.pack(side='right')
        root.protocol('WM_DELETE_WINDOW',self.close)
        self.refresh()
        self.update_request=None
        if not hidden:self.root.after(700,self.check_updates)

    def check_updates(self,manual=False):
        from .updater import find_update
        from . import __version__
        found,errors=find_update(self.controller.paths.root)
        status_file=self.controller.paths.root/'updates/last-update.json'
        if status_file.exists():
            try:status=json.loads(status_file.read_text('utf-8'))
            except (OSError,ValueError):status={}
            if status.get('status')=='failed':
                messagebox.showwarning('更新未完成',status.get('error','更新失败'),parent=self.root)
                try:status_file.unlink(missing_ok=True)
                except OSError:pass
        if found:
            version=found['version']
            if messagebox.askyesno('发现 PaperBridge 更新',
                    f'当前版本 {__version__}，本地更新包版本 {version}。\n\n安装会正常退出并重开程序，任务和结果保留。现在安装吗？',parent=self.root):
                self.update_request=found
                self.close()
        elif manual:
            messagebox.showinfo('检查更新',('没有可用的本地更新包。\n\n'+ '\n'.join(errors[:3])) if errors else
                                f'当前版本 {__version__}，updates 文件夹中没有更新版本。',parent=self.root)

    def add(self,files):
        errors=[]
        for path in files:
            try: self.controller.submit(path)
            except Exception as exc: errors.append(f'{path}: {exc}')
        if errors: messagebox.showerror('添加文件','\n'.join(errors)[:1500])
        self.refresh_once()

    def selection(self): return list(self.tree.selection())
    def resize_columns(self,width):
        status=205 if width>=970 else 170
        version=65;progress=65;elapsed=105
        title=max(220,width-status-version-progress-elapsed-8)
        for key,size in [('title',title),('status',status),('version',version),('progress',progress),('elapsed',elapsed)]:
            if abs(self.tree.column(key,'width')-size)>2:self.tree.column(key,width=size)
    def start(self):
        scope=self.selection() or list(self.tree.get_children())
        selected=[ident for ident in scope if self.controller.jobs[ident]['status'] in ('ready','paused','failed','partial')]
        try:
            for ident in selected: self.controller.enqueue(ident)
        except Exception as exc:messagebox.showerror('开始翻译',str(exc),parent=self.root)
    def pause(self):
        with self.controller.lock:active=list(self.controller.active)
        selected=[ident for ident in self.selection() if ident in active]
        for ident in selected or active:self.controller.pause(ident)
    def refresh_once(self):
        jobs=self.controller.list_jobs()
        query=self.query.get().strip().casefold();status_filter=self.status_filter.get()
        visible=set()
        for job in jobs:
            state=job['status']
            group='进行中' if state in ('running','queued') else '已完成' if state=='complete' else '待处理'
            if status_filter!='全部' and group!=status_filter:continue
            if query and query not in (job['title']+' '+job.get('source_name','')).casefold():continue
            values=(job['title'],job.get('stage') or state,
                    'v'+str(job.get('revision',1)) if job.get('outputs') else '—',
                    f'{job.get("progress",0)*100:.0f}%',duration_label(job))
            if self.tree.exists(job['id']):self.tree.item(job['id'],values=values)
            else:self.tree.insert('','end',iid=job['id'],values=values)
            visible.add(job['id'])
        for ident in self.tree.get_children():
            if ident not in visible:self.tree.delete(ident)
        self.empty.configure(text='没有匹配的文献' if jobs else '暂无文献任务')
        if visible:self.empty.place_forget()
        else:self.empty.place(relx=.5,rely=.45,anchor='center')
        complete=sum(j['status']=='complete' for j in jobs)
        self.summary.configure(text=f'{len(jobs)} 篇 · 已完成 {complete}')
        self.update_actions()
        self.show_details()
        text='Zotero 已连接' if self.bridge.connected else 'Zotero 未连接 · 可独立翻译'
        self.connection.configure(foreground=MUTED)
        if self.bridge.connected:
            from . import __version__
            from .updater import version_key
            plugin_version=self.bridge.details.get('pluginVersion')
            try:
                if version_key(plugin_version)<version_key(__version__):
                    text+=f' · 插件 v{plugin_version} 待更新'
                    self.connection.configure(foreground=WARNING)
                else:self.connection.configure(foreground=MUTED)
            except ValueError:pass
        if self.controller.local.backend: text+=' · 本地 '+self.controller.local.backend.upper()
        self.connection.configure(text=text)
        self.runtime_status.configure(text=f'并行 {len(self.controller.active)}/{self.controller.parallel_limit()}')
    def update_actions(self):
        selected=self.selection()
        busy=any(self.controller.jobs[i]['status'] in ('running','queued') for i in selected)
        scope=selected or list(self.tree.get_children())
        can_start=any(self.controller.jobs[i]['status'] in ('ready','paused','failed','partial') for i in scope)
        self.start_button.configure(state='normal' if can_start else 'disabled')
        self.pause_button.configure(state='normal' if self.controller.active else 'disabled')
        for label,button in self.job_buttons.items():
            available=bool(selected) and (not busy or label=='查看 PDF')
            if label=='查看 PDF':available=True
            button.configure(state='normal' if available else 'disabled')
    def refresh(self):
        self.refresh_once(); self.refresh_timer=self.root.after(1400,self.refresh)
    def show_details(self):
        ids=self.selection()
        self.update_actions()
        if not ids:self.detail.set('');return
        j=self.controller.jobs[ids[0]]
        detail=j.get('error') or j.get('import_error') or j['source_name']
        if not (j.get('error') or j.get('import_error')):
            detail+=f'  ·  v{j.get("revision",1)}  ·  {len(j.get("review_flags",[]))} 处待核对'
            if j.get('gpt_cost'):detail+=f'  ·  GPT ${j["gpt_cost"]:.4f}'
        detail+='  ·  耗时 '+duration_label(j)
        self.detail.set(detail)

    def usage_table(self):
        win=tk.Toplevel(self.root);win.title('处理时间与用量 · PaperBridge')
        win.geometry('1160x580');win.minsize(900,460);win.transient(self.root)
        heading=ttk.Frame(win,padding=(18,16,18,8));heading.pack(fill='x')
        ttk.Label(heading,text='处理时间与用量',font=('Microsoft YaHei UI',15,'bold')).pack(side='left')
        summary=ttk.Label(win,text='',foreground=MUTED,padding=(18,0,18,12))
        summary.pack(fill='x')
        frame=ttk.Frame(win,padding=(18,0,18,0));frame.pack(fill='both',expand=True)
        columns=(('title','文献',270),('elapsed','耗时',105),('free_segments','免费译段',85),
                 ('free_chars','免费字符',95),('free_calls','免费调用',85),('paid_calls','GPT 调用',85),
                 ('input','输入 Token',100),('output','输出 Token',100),
                 ('paid','GPT 估算 $',100),('reserved','待确认 $',95))
        table=ttk.Treeview(frame,columns=[item[0] for item in columns],show='headings')
        for key,label,width in columns:
            table.heading(key,text=label)
            table.column(key,width=width,minwidth=width,stretch=key=='title',anchor='w' if key=='title' else 'center')
        scroll_y=ttk.Scrollbar(frame,orient='vertical',command=table.yview)
        scroll_x=ttk.Scrollbar(frame,orient='horizontal',command=table.xview)
        table.configure(yscrollcommand=scroll_y.set,xscrollcommand=scroll_x.set)
        table.grid(row=0,column=0,sticky='nsew')
        scroll_y.grid(row=0,column=1,sticky='ns')
        scroll_x.grid(row=1,column=0,sticky='ew')
        frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        note=ttk.Label(win,foreground=MUTED,padding=(18,10,18,16),
                       text='免费译段含缓存复用；旧任务的服务调用和完整耗时未记录。* 表示仅统计新版运行时间。GPT 金额按调用回执估算，非服务商账单。')
        note.pack(fill='x')
        note.bind('<Configure>',lambda event:note.configure(wraplength=max(400,event.width-36)))
        def update():
            if not win.winfo_exists():return
            jobs=self.controller.list_jobs()
            total_free=total_calls=total_paid=0
            from decimal import Decimal
            amount=reserved=Decimal(0)
            for job in jobs:
                usage=job_usage(job,self.controller.paths.jobs)
                total_free+=usage['free_segments'];total_calls+=usage['free_calls'] or 0
                total_paid+=usage['paid_calls'];amount+=usage['paid_usd'];reserved+=usage['reserved_usd']
                values=(job['title'],duration_label(job),usage['free_segments'],usage['free_chars'],
                        usage['free_calls'] if usage['free_calls'] is not None else '未记录',
                        '未记录' if usage['token_history_missing'] else usage['paid_calls'],
                        '未记录' if usage['token_history_missing'] else usage['input_tokens'],
                        '未记录' if usage['token_history_missing'] else usage['output_tokens'],
                        f'{usage["paid_usd"]:.5f}',f'{usage["reserved_usd"]:.5f}')
                if table.exists(job['id']):table.item(job['id'],values=values)
                else:table.insert('','end',iid=job['id'],values=values)
            for ident in table.get_children():
                if ident not in {job['id'] for job in jobs}:table.delete(ident)
            summary.configure(text=f'{len(jobs)} 篇文献  ·  免费译段 {total_free}  ·  新版已记录免费调用 {total_calls}  ·  GPT 调用 {total_paid}  ·  估算 ${amount:.5f}  ·  待确认 ${reserved:.5f}')
            win.after(2000,update)
        update()
    def open_result(self):
        ids=self.selection()
        if ids:
            j=self.controller.jobs[ids[0]]
            if j.get('outputs'): os.startfile(str(pathlib.Path(j['outputs']['pdf']).parent)); return
        os.startfile(str(self.controller.paths.results))

    def link(self):
        from .zotero_detect import discover
        detected=discover()
        plugin=self.controller.paths.root/'zotero/PaperBridge.xpi'
        if not plugin.exists(): plugin=self.controller.paths.root/'PaperBridge.xpi'
        info=('已检测到 Zotero 和 '+str(len(detected['profiles']))+' 个配置。\n\n') if detected['executable'] or detected['profiles'] else ''
        messagebox.showinfo('Zotero 插件',info+'在 Zotero 的「工具 → 插件」中，点击右上角齿轮并选择「从文件安装插件」，打开随包的 PaperBridge.xpi。按提示重启 Zotero 后，窗口底部会显示连接状态。\n\nZotero 的在线自动更新尚未配置；桌面程序会准备好本地插件包。',parent=self.root)
        if plugin.exists(): os.startfile(str(plugin.parent))

    def settings(self):
        win=tk.Toplevel(self.root);win.title('设置 · PaperBridge');win.geometry('660x450');win.minsize(600,430)
        win.configure(bg=BG);win.transient(self.root)
        s=self.controller.store.settings;vars={};booleans={}
        choices={'mode':('免费联网翻译（推荐）','免费初译＋本地语境检查','完全离线'),
                 'service':('bing','cnki'),'gpu':('auto','cpu','vulkan'),
                 'gpt_model':('gpt-5.6-luna','gpt-5.6-terra','gpt-5.6-sol'),
                 'parallel_documents':('1','2','3')}
        mode_map={'免费联网翻译（推荐）':'free','免费初译＋本地语境检查':'hybrid','完全离线':'offline'}
        notebook=ttk.Notebook(win);notebook.pack(fill='both',expand=True,padx=18,pady=(18,8))
        tabs={name:ttk.Frame(notebook,padding=(22,20)) for name in ('翻译','质量','输出')}
        for name,frame in tabs.items():
            frame.columnconfigure(1,weight=1)
            notebook.add(frame,text=name)
        def field(tab,row,key,label):
            frame=tabs[tab];ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',pady=10)
            value=s[key]
            if key=='mode':value=next(name for name,stored in mode_map.items() if stored==value)
            var=tk.StringVar(value=str(value));vars[key]=var
            widget=(ttk.Combobox(frame,textvariable=var,values=choices[key],state='readonly')
                    if key in choices else ttk.Entry(frame,textvariable=var))
            widget.grid(row=row,column=1,sticky='ew',padx=(18,0),pady=10)
            return widget
        def check(tab,row,key,label):
            var=tk.BooleanVar(value=s[key]);booleans[key]=var
            ttk.Checkbutton(tabs[tab],text=label,variable=var).grid(
                row=row,column=0,columnspan=2,sticky='w',pady=10)
            return var
        field('翻译',0,'mode','翻译模式')
        field('翻译',1,'service','免费翻译服务')
        field('翻译',2,'parallel_documents','并行文献数')
        field('翻译',3,'gpu','本地计算方式')
        check('质量',0,'local_review','本地检查语境和句义')
        check('质量',1,'local_fallback','免费服务失败时使用本地模型')
        gpt_enabled=check('质量',2,'gpt_enabled','启用 GPT 校核（可能产生费用）')
        gpt_model=field('质量',3,'gpt_model','GPT 模型')
        gpt_budget=field('质量',4,'gpt_budget','每篇估算上限（美元）')
        def update_gpt_state(*_):
            gpt_model.configure(state='readonly' if gpt_enabled.get() else 'disabled')
            gpt_budget.configure(state='normal' if gpt_enabled.get() else 'disabled')
        gpt_enabled.trace_add('write',update_gpt_state);update_gpt_state()
        ttk.Label(tabs['质量'],text='费用按实际调用估算，非服务商账单。',foreground=MUTED).grid(
            row=5,column=0,columnspan=2,sticky='w',pady=(12,0))
        field('输出',0,'output_dir','输出目录')
        def choose_output():
            folder=filedialog.askdirectory(parent=win,initialdir=str(self.controller.paths.root))
            if folder:vars['output_dir'].set(folder)
        ttk.Button(tabs['输出'],text='选择目录',command=choose_output).grid(row=1,column=1,sticky='w',padx=(18,0))
        check('输出',2,'auto_import','完成后自动附回 Zotero')
        def save():
            try:
                new={k:v.get() for k,v in vars.items()}; new['mode']=mode_map[new['mode']]
                new['gpt_budget']=float(new['gpt_budget'])
                new['parallel_documents']=int(new['parallel_documents'])
                if not 1<=new['parallel_documents']<=3:raise ValueError('同时处理文献数需为 1–3')
                if new['gpt_budget']<0: raise ValueError('预算不能小于零')
                new.update({k:v.get() for k,v in booleans.items()})
                self.controller.store.save_settings(new)
                # Ready jobs use the latest configuration; active/resumable jobs retain their settings.
                with self.controller.lock:
                    for job in self.controller.jobs.values():
                        if job['status']=='ready': job['settings']=dict(self.controller.store.settings); self.controller.save_job(job)
                win.destroy()
            except Exception as exc: messagebox.showerror('保存设置',str(exc),parent=win)
        buttons=ttk.Frame(win,padding=(18,4,18,18));buttons.pack(fill='x')
        ttk.Button(buttons,text='保存设置',style='Accent.TButton',command=save).pack(side='right')
        ttk.Button(buttons,text='取消',command=win.destroy).pack(side='right',padx=(0,8))

    def edit(self):
        ids=self.selection()
        if not ids: return
        job=self.controller.jobs[ids[0]]
        if job['status'] in ['running','queued']:
            messagebox.showinfo('校对','请先暂停任务，待当前段落保存后再编辑。'); return
        if not job['segments']:
            messagebox.showinfo('校对','文献尚未完成提取，请先开始处理。'); return
        win=tk.Toplevel(self.root); win.title('对照校对 · '+job['title']); win.geometry('1120x760')
        top=ttk.Frame(win,padding=12); top.pack(fill='x')
        label=ttk.Label(top,text=''); label.pack(side='left')
        index=tk.IntVar(value=0)
        panes=ttk.Panedwindow(win,orient='horizontal');panes.pack(fill='both',expand=True,padx=12)
        source=tk.Text(panes,wrap='word',font=('Microsoft YaHei UI',11),padx=14,pady=12,undo=True,exportselection=False)
        target=tk.Text(panes,wrap='word',font=('Microsoft YaHei UI',11),padx=14,pady=12,undo=True,exportselection=False)
        panes.add(source,weight=1);panes.add(target,weight=1)
        warning=tk.StringVar();ttk.Label(win,textvariable=warning,wraplength=1080,padding=12).pack(fill='x')
        controls=ttk.Frame(win,padding=12);controls.pack(fill='x')
        def load():
            seg=job['segments'][index.get()]
            label.configure(text=f'{index.get()+1}/{len(job["segments"])} · 原文 p.{seg["page"]} · {seg["id"]}（左为原文，右为可编辑译文）')
            source.delete('1.0','end');source.insert('1.0',seg['source']);target.delete('1.0','end');target.insert('1.0',seg.get('translation',''))
            warning.set('；'.join(seg.get('issues',[])))
        def save():
            seg=job['segments'][index.get()]
            seg['source']=source.get('1.0','end-1c').strip();seg['translation']=target.get('1.0','end-1c').strip()
            if seg['translation']: seg['status']='done'
            seg['human_edited']=True;seg['issues']=[]
            self.controller.save_job(job)
        def move(delta): save();index.set(max(0,min(len(job['segments'])-1,index.get()+delta)));load()
        def split():
            seg=job['segments'][index.get()]
            position=source.count('1.0','insert','chars')
            cut=position[0] if position else 0
            text=source.get('1.0','end-1c')
            if cut<=0 or cut>=len(text): return messagebox.showinfo('拆分','请把光标放在原文要拆分的位置。',parent=win)
            import copy,secrets
            new=copy.deepcopy(seg);new['id']=seg['id']+'-'+secrets.token_hex(3);new['source']=text[cut:].strip();new['translation']='';new['status']='pending'
            seg['source']=text[:cut].strip();seg['translation']='';seg['status']='pending'
            for piece in [seg,new]:
                piece['issues']=[]
                for key in ['local_checked','gpt_checked','semantic_flags','initial_translation','source_ranges']:piece.pop(key,None)
            job['segments'].insert(index.get()+1,new);self.controller.save_job(job);load()
        def merge():
            i=index.get()
            if i+1>=len(job['segments']): return
            save();a,b=job['segments'][i:i+2]
            if a['type']=='graphic' or b['type']=='graphic': return
            if a['page']!=b['page'] or min(a['bbox'][2],b['bbox'][2])-max(a['bbox'][0],b['bbox'][0])<=0:
                return messagebox.showinfo('合并','为保持原页对应，请只合并同一页、同一栏内的段落。',parent=win)
            a['bbox']=[min(a['bbox'][0],b['bbox'][0]),min(a['bbox'][1],b['bbox'][1]),max(a['bbox'][2],b['bbox'][2]),max(a['bbox'][3],b['bbox'][3])]
            a['source']+=' '+b['source'];a['translation']='';a['status']='pending';a['local_checked']=False
            a['issues']=[]
            for key in ['gpt_checked','semantic_flags','initial_translation']:a.pop(key,None)
            job['segments'].pop(i+1);self.controller.save_job(job);load()
        def export():
            save()
            try:self.controller.export_again(job['id']);messagebox.showinfo('导出','已保存新版双语 PDF。',parent=win)
            except Exception as exc:messagebox.showerror('导出',str(exc),parent=win)
        def confirm_term():
            try:s=source.get('sel.first','sel.last');t=target.get('sel.first','sel.last')
            except tk.TclError:return messagebox.showinfo('确认译法','请分别在原文和译文中选中对应术语。',parent=win)
            self.controller.store.confirm_term(job.get('context',{}).get('domain',''),s,t,job['segments'][index.get()]['source'])
            messagebox.showinfo('确认译法','已保存为本领域、当前含义的参考译法。',parent=win)
        for text,command in [('上一段',lambda:move(-1)),('下一段',lambda:move(1)),('保存修改',save),('拆分段落',split),('合并下一段',merge),('确认术语',confirm_term),('导出新版',export)]:
            ttk.Button(controls,text=text,command=command).pack(side='left',padx=3)
        load()

    def reexport(self):
        ids=self.selection()
        if not ids:return
        if any(self.controller.jobs[i]['status'] in ('running','queued') for i in ids):
            return messagebox.showinfo('导出','请先暂停任务，待当前段落保存后再导出。')
        def worker():
            try:
                for ident in ids:self.controller.export_again(ident)
                self.root.after(0,lambda:messagebox.showinfo('导出','已按原页布局生成新版双语 PDF。'))
            except Exception as exc:
                message=str(exc)
                self.root.after(0,lambda:messagebox.showerror('导出',message))
        threading.Thread(target=worker,daemon=True).start()

    def import_selected(self):
        ids=self.selection()
        if not ids:return
        if any(self.controller.jobs[i]['status'] in ('running','queued') for i in ids):
            return messagebox.showinfo('附回 Zotero','请等待所选任务完成，或暂停并保存后再附回。')
        if not self.bridge.connected:return self.link()
        def worker():
            messages=[]
            for ident in ids:
                job=self.controller.jobs[ident]
                if not job.get('outputs'):continue
                try:
                    r=self.bridge.request('import',{'path':job['outputs']['pdf'],'locator':job.get('locator'),
                        'metadata':job['metadata'],'title':job['title'],'fingerprint':job['fingerprint'],'job_id':ident,
                        'revision':job.get('revision',1),'source_path':str(self.controller.paths.jobs/ident/'source.pdf'),
                        'source_name':job['source_name']},timeout=180)
                    job['zotero_result']=r;job.pop('import_error',None);self.controller.save_job(job)
                    messages.append(job['title']+'：已附回')
                except Exception as exc:messages.append(str(exc))
            self.root.after(0,lambda:messagebox.showinfo('Zotero 归档','\n'.join(messages)[:1800]))
        threading.Thread(target=worker,daemon=True).start()

    def close(self):
        if self.controller.active and not messagebox.askyesno('退出','当前任务会在已保存的位置暂停，是否退出？'):
            self.update_request=None
            return
        self.controller.close();self.bridge.close()
        if self.update_request:
            package=self.update_request['package'];version=self.update_request['version']
            try:subprocess.Popen([sys.executable,'--apply-update',str(package),version,str(os.getpid())],
                                  cwd=self.controller.paths.root,
                                  creationflags=getattr(subprocess,'DETACHED_PROCESS',0))
            except OSError as exc:
                messagebox.showerror('安装更新',f'无法启动更新程序：{exc}',parent=self.root)
        if getattr(self,'refresh_timer',None):self.root.after_cancel(self.refresh_timer)
        self.root.destroy()
    def run(self):self.root.mainloop()
