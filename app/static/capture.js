/* Explicit one-frame screen capture. No network requests, persisted media stream,
 * clipboard polling or automatic upload. The caller receives a confirmed File. */
'use strict';
window.BNUCapture = (() => {
  let busy = false, cancelPreview = null, generation = 0;
  function message(error) {
    if (error?.name === 'NotAllowedError' || error?.name === 'AbortError')
      return '截屏已取消或未获授权，没有生成或上传图片。可使用系统截图后粘贴。';
    if (error?.name === 'NotReadableError')
      return '无法读取所选屏幕。请检查系统的屏幕录制权限，或使用系统截图后粘贴。';
    return error?.message || '截屏失败，请使用系统截图后粘贴或选择图片文件。';
  }
  async function frame() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getDisplayMedia)
      throw new Error('此浏览器或连接不支持网页截屏。请通过 HTTPS 或 localhost 使用支持屏幕共享的桌面浏览器；也可用系统截图后在上传区域粘贴。');
    let stream, video;
    try {
      // Must be invoked directly from a deliberate click (transient activation).
      stream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: false});
      video = document.createElement('video');
      video.muted = true; video.playsInline = true;
      video.className = 'capture-video'; document.body.append(video);
      video.srcObject = stream;
      await video.play();
      if (video.requestVideoFrameCallback) {
        await new Promise((resolve,reject) => {
          let id;
          const timer=setTimeout(() => {
            if (id !== undefined) video.cancelVideoFrameCallback(id);
            reject(new Error('等待屏幕画面超时；没有生成或上传图片，请重试。'));
          },10000);
          id=video.requestVideoFrameCallback(() => {clearTimeout(timer);resolve();});
        });
      }
      if (!video.videoWidth || !video.videoHeight || video.readyState < 2)
        throw new Error('没有取得可读取的屏幕画面。请重新选择窗口。');
      if (video.videoWidth * video.videoHeight > 40000000)
        throw new Error('屏幕图片超过4000万像素，请改选单个窗口或使用系统区域截图。');
      const canvas=document.createElement('canvas');
      canvas.width=video.videoWidth;canvas.height=video.videoHeight;
      canvas.getContext('2d').drawImage(video,0,0);
      return canvas;
    } finally {
      // Always stop every track before showing a preview, including errors.
      stream?.getTracks().forEach(track => track.stop());
      if (video) {video.pause();video.srcObject=null;video.remove();}
    }
  }
  function crop(source) {
    return new Promise(resolve => {
      const previous=document.activeElement;
      const dialog=document.createElement('dialog');dialog.className='capture-dialog';
      dialog.setAttribute('aria-label','确认截图');
      dialog.innerHTML='<form class="capture-form"><div class="modal-head"><h2>确认截图</h2><button type="button" data-cancel class="btn secondary">取消</button></div><div class="modal-body"><p>屏幕共享已停止。请确认图片中没有无关隐私；可在下方拖动框选需要保留的区域。确认后仅加入待上传列表。</p><div class="capture-surface"><canvas tabindex="0" aria-label="截图预览，可用鼠标框选；也可在下方输入裁剪坐标"></canvas></div><div class="capture-fields"><label>左侧像素<input name="x" type="number" min="0" required></label><label>顶部像素<input name="y" type="number" min="0" required></label><label>宽度像素<input name="w" type="number" min="16" required></label><label>高度像素<input name="h" type="number" min="16" required></label></div><p class="small muted">按像素裁剪，不缩小、不改变图片比例。Esc取消；确认前不会发送到服务器。</p><p class="capture-error" role="alert"></p><div class="form-actions"><button type="button" data-reset class="btn secondary">恢复完整图片</button><button type="submit" class="btn">使用所选图片</button></div></div></form>';
      document.body.append(dialog);
      cancelPreview=()=>finish(null);
      const view=dialog.querySelector('canvas'), form=dialog.querySelector('form');
      view.width=source.width;view.height=source.height;
      let rect={x:0,y:0,w:source.width,h:source.height},start=null,closed=false;
      const ctx=view.getContext('2d');
      function paint() {
        ctx.clearRect(0,0,view.width,view.height);ctx.drawImage(source,0,0);
        ctx.fillStyle='rgba(0,0,0,.35)';
        ctx.fillRect(0,0,view.width,rect.y);ctx.fillRect(0,rect.y+rect.h,view.width,view.height-rect.y-rect.h);
        ctx.fillRect(0,rect.y,rect.x,rect.h);ctx.fillRect(rect.x+rect.w,rect.y,view.width-rect.x-rect.w,rect.h);
        ctx.strokeStyle='#254b75';ctx.lineWidth=Math.max(2,view.width/600);ctx.strokeRect(rect.x,rect.y,rect.w,rect.h);
        for (const key of ['x','y','w','h'])form.elements[key].value=rect[key];
      }
      function finish(file) {
        if (closed)return;closed=true;cancelPreview=null;dialog.close();dialog.remove();source.width=0;source.height=0;
        if(previous?.isConnected)previous.focus();resolve(file);
      }
      function point(event) {
        const b=view.getBoundingClientRect();
        return {x:Math.round(Math.max(0,Math.min(source.width,(event.clientX-b.left)*source.width/b.width))),
          y:Math.round(Math.max(0,Math.min(source.height,(event.clientY-b.top)*source.height/b.height)))};
      }
      view.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();start=point(e);view.setPointerCapture(e.pointerId);});
      view.addEventListener('pointermove',e=>{if(!start)return;const p=point(e);rect={x:Math.min(p.x,start.x),y:Math.min(p.y,start.y),w:Math.abs(p.x-start.x),h:Math.abs(p.y-start.y)};paint();});
      view.addEventListener('pointerup',()=>{start=null;});
      view.addEventListener('pointercancel',()=>{start=null;});
      form.addEventListener('change',()=>{
        for(const key of ['x','y','w','h'])rect[key]=Math.round(Number(form.elements[key].value));
        if(Object.values(rect).every(Number.isFinite))paint();
      });
      dialog.querySelector('[data-reset]').onclick=()=>{rect={x:0,y:0,w:source.width,h:source.height};paint();};
      dialog.querySelector('[data-cancel]').onclick=()=>finish(null);
      dialog.addEventListener('cancel',e=>{e.preventDefault();finish(null);});
      form.addEventListener('submit',async e=>{
        e.preventDefault();
        for(const key of ['x','y','w','h'])rect[key]=Math.round(Number(form.elements[key].value));
        if(!Object.values(rect).every(Number.isFinite)||rect.x<0||rect.y<0||rect.w<16||rect.h<16||rect.x+rect.w>source.width||rect.y+rect.h>source.height){
          dialog.querySelector('.capture-error').textContent='裁剪范围须位于图片以内，宽高至少16像素。';return;
        }
        const button=form.querySelector('[type=submit]');if(button.disabled)return;button.disabled=true;
        const out=document.createElement('canvas');out.width=rect.w;out.height=rect.h;
        out.getContext('2d').drawImage(source,rect.x,rect.y,rect.w,rect.h,0,0,rect.w,rect.h);
        const blob=await new Promise(r=>out.toBlob(r,'image/png'));out.width=0;out.height=0;
        if(!blob){button.disabled=false;dialog.querySelector('.capture-error').textContent='无法生成图片，请重试。';return;}
        finish(new File([blob],'截图-'+new Date().toISOString().replace(/[:.]/g,'-')+'.png',{type:'image/png'}));
      });
      paint();dialog.showModal();dialog.querySelector('[type=submit]').focus();
    });
  }
  async function take() {
    if(busy)throw new Error('已有截屏操作正在进行，请先完成或取消。');
    busy=true;
    try{const epoch=generation, source=await frame();if(epoch!==generation){source.width=0;source.height=0;return null;}return await crop(source);}
    catch(e){throw new Error(message(e));}
    finally{busy=false;}
  }
  return {take,cancel(){generation++;cancelPreview?.();}};
})();
