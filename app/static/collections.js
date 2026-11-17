/* Consistent collection selection and context actions. Works with native tables,
 * cards and checkbox lists. Never changes server data without a supplied action. */
'use strict';
window.BNUCollections = (() => {
  let active=null, menu=null, drag=null, frameId=null, completedDrag=null;
  const interactive='a,button,input,textarea,select,label,[contenteditable="true"],[data-no-select]';
  function button(text,fn,cls='btn secondary small-btn') {
    const b=document.createElement('button');b.type='button';b.className=cls;b.textContent=text;
    b.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();fn(e);});return b;
  }
  function visible(row){return !row.hidden&&getComputedStyle(row).display!=='none';}
  function closeMenu(restore=true){if(!menu)return;const m=menu;menu=null;m.el.remove();if(restore&&m.returnTo?.isConnected)m.returnTo.focus();}
  function notify(error){window.dispatchEvent(new CustomEvent('collection-error',{detail:error}));}
  async function invoke(action){try{await action();}catch(e){notify(e);}}
  function menuAt(collection,x,y,anchor){
    closeMenu(false);active=collection;
    const el=document.createElement('div');el.className='collection-menu';el.setAttribute('role','menu');el.setAttribute('aria-label','所选项目操作');
    // A dialog is a separate top layer; its menus must stay inside that layer.
    (collection.root.closest('dialog')||document.body).append(el);
    const options=collection.actions();
    for(const option of options){
      if(option.disabled)continue;
      const b=button(option.label,()=>{closeMenu();invoke(option.run);},'collection-menu-item'+(option.danger?' danger':''));
      b.setAttribute('role','menuitem');b.tabIndex=-1;
      el.append(b);
    }
    el.style.left=Math.max(4,Math.min(x,innerWidth-el.offsetWidth-8))+'px';
    el.style.top=Math.max(4,Math.min(y,innerHeight-el.offsetHeight-8))+'px';
    menu={el,returnTo:anchor};el.querySelector('button')?.focus();
  }
  class Collection {
    constructor(root,options){
      this.root=root;this.options=options;this.selected=new Set(options.selectedKeys||[]);this.anchor=null;this.mode=false;
      root._collection=this;root.dataset.selectionScope='1';root.tabIndex=0;
      this.bar=document.createElement('div');this.bar.className='collection-toolbar';this.bar.setAttribute('role','toolbar');this.bar.setAttribute('aria-label','项目选择与批量操作');
      this.count=document.createElement('span');this.count.className='small selection-count';this.count.setAttribute('aria-live','polite');this.bar.append(this.count);
      this.modeButton=button('框选',()=>{this.mode=!this.mode;this.paint();});this.modeButton.setAttribute('aria-pressed','false');this.bar.append(this.modeButton);
      this.bar.append(button(options.pageLabel||'本页全选',()=>{this.select(this.visibleRows().map(r=>r.dataset.pick));}));
      if(options.allKeys)this.bar.append(button('全部筛选结果全选',()=>{this.select(options.allKeys());}));
      this.bar.append(button('反选本页',()=>{for(const row of this.visibleRows()){const id=row.dataset.pick;this.selected.has(id)?this.selected.delete(id):this.selected.add(id);}this.paint();}));
      this.bar.append(button('清空选择',()=>{this.selected.clear();this.paint();}));
      this.more=button('所选项目操作',()=>{const r=this.more.getBoundingClientRect();menuAt(this,r.left,r.bottom,this.more);});
      this.more.setAttribute('aria-haspopup','menu');this.bar.append(this.more);
      const holder=root.closest('.table-wrap')||(root.tagName==='TBODY'?root.closest('table'):root);holder.before(this.bar);
      this.bar.addEventListener('focusin',()=>active=this);
      root.addEventListener('focusin',()=>active=this);
      root.addEventListener('click',e=>this.click(e));
      root.addEventListener('pointerdown',e=>this.down(e));
      root.addEventListener('contextmenu',e=>{
        if(e.shiftKey||e.target.closest('input,textarea,select,[contenteditable=true]'))return;
        const row=e.target.closest('[data-pick]');if(!row)return;
        e.preventDefault();e.stopPropagation();active=this;
        if(!this.selected.has(row.dataset.pick))this.select([row.dataset.pick]);
        row.tabIndex=0;menuAt(this,e.clientX,e.clientY,row);
      });
      this.refresh();
      this.addTableTools();
    }
    rows(){return Array.from(this.root.querySelectorAll(this.options.selector)).filter(r=>r.closest('[data-selection-scope]')===this.root);}
    visibleRows(){return this.rows().filter(visible);}
    refresh(){
      const rows=this.rows();
      rows.forEach((row,index)=>{
        row.dataset.pick=String(this.options.key(row,index));
        if(this.options.checkbox){
          const check=row.querySelector(this.options.checkbox);
          if(check)check.classList.add('collection-check');
        }else if(!row.querySelector(':scope > .selection-cell,:scope > .row-picker')){
          const check=document.createElement('input');check.type='checkbox';check.className='collection-check';check.setAttribute('aria-label','选择 '+(row.dataset.search||row.textContent||'项目').trim().slice(0,80));
          const wrapper=document.createElement(row.tagName==='TR'?'td':'span');wrapper.className=row.tagName==='TR'?'selection-cell':'row-picker';wrapper.append(check);row.prepend(wrapper);
        }
      });
      if(this.root.tagName==='TBODY'&&!this.options.checkbox){
        const header=this.root.closest('table')?.querySelector('thead tr');
        if(header&&!header.querySelector('.selection-cell')){const th=document.createElement('th');th.className='selection-cell';th.textContent='选择';header.prepend(th);}
      }
      if(!this.options.allKeys){const known=new Set(rows.map(r=>r.dataset.pick));this.selected=new Set([...this.selected].filter(id=>known.has(id)));}
      this.paint();
    }
    addTableTools(){
      if(this.root.tagName!=='TBODY')return;
      const headers=[...this.root.closest('table').querySelectorAll('thead tr:last-child th')];
      const columns=headers.map((h,index)=>({index,label:h.textContent.trim()})).filter(c=>c.label&&c.label!=='选择'&&c.label!=='操作');
      if(!columns.length)return;
      this.filters=new Map();this.originalOrder=new Map(this.rows().map((r,i)=>[r.dataset.pick,i]));
      const details=document.createElement('details');details.className='collection-filters';
      const summary=document.createElement('summary');summary.textContent='按列筛选与排序';details.append(summary);
      const body=document.createElement('div');body.className='row wrap';details.append(body);
      const select=document.createElement('select');select.setAttribute('aria-label','筛选或排序列');
      for(const c of columns){const opt=document.createElement('option');opt.value=c.index;opt.textContent=c.label;select.append(opt);}
      const input=document.createElement('input');input.type='search';input.placeholder='包含的文字（多列条件同时满足）';input.setAttribute('aria-label','列筛选条件');
      body.append(select,input);
      const apply=()=>{if(input.value.trim())this.filters.set(Number(select.value),input.value.trim().toLocaleLowerCase());else this.filters.delete(Number(select.value));this.applyFilters();};
      body.append(button('应用本列条件',apply),button('清除所有列条件',()=>{this.filters.clear();input.value='';this.applyFilters();}));
      const sort=dir=>{const index=Number(select.value),collator=new Intl.Collator('zh-CN',{numeric:true,sensitivity:'base'});this.rows().sort((a,b)=>dir*collator.compare(this.cellText(a,index),this.cellText(b,index))).forEach(r=>this.root.append(r));this.paint();};
      body.append(button('升序',()=>sort(1)),button('降序',()=>sort(-1)),button('恢复原顺序',()=>{this.rows().sort((a,b)=>(this.originalOrder.get(a.dataset.pick)||0)-(this.originalOrder.get(b.dataset.pick)||0)).forEach(r=>this.root.append(r));this.paint();}));
      input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();apply();}});
      select.onchange=()=>input.value=this.filters.get(Number(select.value))||'';
      this.filterSummary=document.createElement('span');this.filterSummary.className='small';body.append(this.filterSummary);
      this.bar.append(details);
    }
    cellText(row,index){const cell=row.cells[index];if(!cell)return '';return [...cell.querySelectorAll('input,textarea,select')].map(x=>x.type==='checkbox'?(x.checked?'是':'否'):x.value).join(' ')+' '+cell.textContent.trim();}
    applyFilters(){
      for(const row of this.rows()){
        row.dataset.columnHidden=String([...this.filters].some(([i,text])=>!this.cellText(row,i).toLocaleLowerCase().includes(text)));
        row.hidden=row.dataset.searchHidden==='true'||row.dataset.columnHidden==='true';
      }
      this.filterSummary.textContent=[...this.filters].map(([i,text])=>`第${i+1}列包含“${text}”`).join('；')||'未设置列条件';this.paint();
    }
    select(keys){this.selected=new Set(keys);this.paint();}
    range(id,add=false){
      const ids=this.visibleRows().map(r=>r.dataset.pick),a=ids.indexOf(this.anchor),b=ids.indexOf(id);
      const range=a<0||b<0?[id]:ids.slice(Math.min(a,b),Math.max(a,b)+1);
      this.selected=new Set(add?[...this.selected,...range]:range);this.paint();
    }
    click(e){
      if(this.justDragged){this.justDragged=false;e.preventDefault();e.stopPropagation();return;}
      const row=e.target.closest('[data-pick]');if(!row)return;active=this;
      const id=row.dataset.pick;
      if(e.target.matches('.collection-check')){
        if(e.shiftKey)this.range(id,e.ctrlKey||e.metaKey);
        else {e.target.checked?this.selected.add(id):this.selected.delete(id);this.anchor=id;this.paint();}
        e.stopPropagation();return;
      }
      if(e.target.closest(interactive))return;
      if(e.shiftKey)this.range(id,e.ctrlKey||e.metaKey);
      else if(e.ctrlKey||e.metaKey){this.selected.has(id)?this.selected.delete(id):this.selected.add(id);this.anchor=id;this.paint();}
      else if(!window.getSelection()?.toString()){this.anchor=id;this.select([id]);}
    }
    paint(){
      const visibleKeys=new Set(this.visibleRows().map(r=>r.dataset.pick));
      let hidden=0;for(const id of this.selected)if(!visibleKeys.has(id))hidden++;
      this.count.textContent='已选 '+this.selected.size+' 项'+(hidden?'（含不在本页/筛选中 '+hidden+' 项）':'');
      this.root.classList.toggle('box-select-mode',this.mode);this.modeButton.setAttribute('aria-pressed',String(this.mode));
      for(const row of this.rows()){
        const picked=this.selected.has(row.dataset.pick);row.classList.toggle('is-selected',picked);row.setAttribute('aria-selected',String(picked));
        const input=row.querySelector('.collection-check');if(input)input.checked=picked;
      }
      this.options.onChange?.(Array.from(this.selected));
    }
    down(e){
      active=this;
      if(e.button!==0||e.pointerType==='touch'||e.target.closest(this.mode?'a,button,input,textarea,select,[contenteditable=true],[data-no-select]':interactive))return;
      if(!this.mode&&e.target!==this.root)return;
      e.preventDefault();closeMenu(false);
      const bounds=this.root.getBoundingClientRect();
      const box=document.createElement('div');box.className='selection-rectangle';(this.root.closest('dialog')||document.body).append(box);
      drag={collection:this,base:new Set(this.selected),anchorX:e.clientX-bounds.left,anchorY:e.clientY-bounds.top,
        x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,add:e.ctrlKey||e.metaKey||e.shiftKey,toggle:e.ctrlKey||e.metaKey,moved:false,box,pointerId:e.pointerId};
      this.root.setPointerCapture(e.pointerId);
      frameId=requestAnimationFrame(tick);
    }
    actions(){
      const ids=Array.from(this.selected),chosen=this.rows().filter(r=>this.selected.has(r.dataset.pick));
      const out=this.options.actions?.(ids,chosen)||[];
      if(ids.length===1&&chosen.length===1){
        const opener=chosen[0].querySelector('a[href],button[data-action]');
        if(opener&&!out.some(x=>x.label==='打开'))out.unshift({label:'打开',run:()=>opener.click()});
      }
      if(chosen.length){out.push({label:'复制所选可见字段',run:()=>copyRows(chosen)});out.push({label:'导出所选可见字段 CSV',run:()=>exportRows(chosen)});}
      out.push({label:'全选当前显示项目',run:()=>this.select(this.visibleRows().map(r=>r.dataset.pick))});
      out.push({label:'清空选择',run:()=>{this.selected.clear();this.paint();}});
      return out;
    }
  }
  function rowValues(row){
    const cells=row.tagName==='TR'?Array.from(row.cells).filter(c=>!c.classList.contains('selection-cell')):[row];
    return cells.map(cell=>{
      const copy=cell.cloneNode(true);copy.querySelectorAll('button,.row-picker,.selection-cell').forEach(x=>x.remove());
      copy.querySelectorAll('input,select,textarea').forEach((x,i)=>{const actual=cell.querySelectorAll('input,select,textarea')[i];x.replaceWith(document.createTextNode(actual?.type==='checkbox'?(actual.checked?'是':'否'):actual?.value||''));});
      return copy.innerText?.trim()||copy.textContent.trim();
    });
  }
  async function copyRows(rows){
    const text=rows.map(r=>rowValues(r).join('\t')).join('\n');
    try{await navigator.clipboard.writeText(text);}catch{
      window.dispatchEvent(new CustomEvent('collection-copy',{detail:text}));
    }
  }
  function exportRows(rows){
    const table=rows[0]?.closest('table'),headers=table?.querySelector('thead tr:last-child');
    const data=[...(headers?[rowValues(headers)]:[['已选项目']]),...rows.map(rowValues)];
    const csv='\ufeff'+data.map(r=>r.map(v=>'"'+String(v).replace(/^[=+@\-\t\r]/,"'$&").replace(/"/g,'""')+'"').join(',')).join('\r\n');
    window.dispatchEvent(new CustomEvent('collection-download',{detail:{blob:new Blob([csv],{type:'text/csv;charset=utf-8'}),name:'所选可见字段.csv'}}));
  }
  function updateDrag(){
    if(!drag)return;const d=drag,b=d.collection.root.getBoundingClientRect();
    if(Math.abs(d.x-d.startX)+Math.abs(d.y-d.startY)>5)d.moved=true;
    if(!d.moved)return;
    const sx=b.left+d.anchorX,sy=b.top+d.anchorY,r={left:Math.min(sx,d.x),right:Math.max(sx,d.x),top:Math.min(sy,d.y),bottom:Math.max(sy,d.y)};
    Object.assign(d.box.style,{left:r.left+'px',top:r.top+'px',width:r.right-r.left+'px',height:r.bottom-r.top+'px'});
    const selected=d.add?new Set(d.base):new Set();
    for(const row of d.collection.visibleRows()){
      const b=row.getBoundingClientRect();
      if(b.right>r.left&&b.left<r.right&&b.bottom>r.top&&b.top<r.bottom){
        if(d.toggle&&d.base.has(row.dataset.pick))selected.delete(row.dataset.pick);else selected.add(row.dataset.pick);
      }
    }
    d.collection.selected=selected;d.collection.paint();
  }
  function tick(){
    if(!drag)return;
    if(!drag.collection.root.isConnected){finishDrag(true);return;}
    const root=drag.collection.root;let parent=root.parentElement;
    while(parent&&!(parent.scrollHeight>parent.clientHeight+1&&/(auto|scroll)/.test(getComputedStyle(parent).overflowY)))parent=parent.parentElement;
    const box=parent?parent.getBoundingClientRect():{top:0,bottom:innerHeight};
    const speed=drag.y<Math.max(0,box.top)+36?-14:drag.y>Math.min(innerHeight,box.bottom)-36?14:0;
    if(speed){if(parent)parent.scrollTop+=speed;else window.scrollBy(0,speed);}
    let horizontal=root.parentElement;
    while(horizontal&&!(horizontal.scrollWidth>horizontal.clientWidth+1&&/(auto|scroll)/.test(getComputedStyle(horizontal).overflowX)))horizontal=horizontal.parentElement;
    if(horizontal){const area=horizontal.getBoundingClientRect();const dx=drag.x<Math.max(0,area.left)+36?-14:drag.x>Math.min(innerWidth,area.right)-36?14:0;if(dx)horizontal.scrollLeft+=dx;}
    updateDrag();frameId=requestAnimationFrame(tick);
  }
  function finishDrag(cancel){
    if(!drag)return;const d=drag;drag=null;cancelAnimationFrame(frameId);d.box.remove();
    if(d.collection.root.hasPointerCapture(d.pointerId))d.collection.root.releasePointerCapture(d.pointerId);
    if(cancel)d.collection.selected=d.base;d.collection.justDragged=d.moved;completedDrag=d.collection;d.collection.paint();

  }
  document.addEventListener('pointermove',e=>{if(drag&&e.pointerId===drag.pointerId){drag.x=e.clientX;drag.y=e.clientY;updateDrag();}});
  document.addEventListener('pointerup',()=>{finishDrag(false);const previous=completedDrag;completedDrag=null;setTimeout(()=>{if(previous)previous.justDragged=false;},0);});
  document.addEventListener('pointercancel',()=>{finishDrag(true);const previous=completedDrag;completedDrag=null;setTimeout(()=>{if(previous)previous.justDragged=false;},0);});
  document.addEventListener('pointerdown',e=>{if(menu&&!menu.el.contains(e.target))closeMenu(false);},true);
  window.addEventListener('blur',()=>{finishDrag(true);closeMenu(false);});
  window.addEventListener('resize',()=>closeMenu(false));
  document.addEventListener('keydown',e=>{
    if(e.isComposing)return;
    if(menu){
      const items=Array.from(menu.el.querySelectorAll('[role=menuitem]'));let index=items.indexOf(document.activeElement);
      if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
        e.preventDefault();e.stopPropagation();index=e.key==='Home'?0:e.key==='End'?items.length-1:(index+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items[index]?.focus();return;
      }
      if(e.key==='Escape'||e.key==='Tab'){if(e.key==='Escape')e.preventDefault();closeMenu();e.stopPropagation();return;}
    }
    if(e.key==='Escape'&&drag){e.preventDefault();e.stopPropagation();finishDrag(true);return;}
    if(!active?.root.isConnected||e.target.closest('input,textarea,select,[contenteditable=true]'))return;
    if(!active.root.contains(e.target)&&!active.bar.contains(e.target))return;
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'){e.preventDefault();active.select(active.visibleRows().map(r=>r.dataset.pick));}
    if((e.shiftKey&&e.key==='F10')||e.key==='ContextMenu'){
      e.preventDefault();const row=e.target.closest('[data-pick]');if(row&&!active.selected.has(row.dataset.pick))active.select([row.dataset.pick]);
      const b=e.target.getBoundingClientRect();menuAt(active,b.left,b.bottom,e.target);
    }
    if(e.key==='Escape'&&active.selected.size){e.preventDefault();active.selected.clear();active.paint();}
  });
  return {
    mount(root,options){if(!root)return null;if(root._collection){root._collection.options=options;root._collection.refresh();return root._collection;}return new Collection(root,options);},
    selected(root){return Array.from(root?._collection?.selected||[]);},
    refresh(root){root?._collection?.refresh();},
    close(){closeMenu(false);finishDrag(true);active=null;},
  };
})();
