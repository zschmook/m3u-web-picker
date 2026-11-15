const SportsTileLogic = (() => {
  function layout(states, weighted, width) {
    const pitch=60, approximateScale=Math.max(.3,(width-24)/720);
    const emptySide=Math.max(30,17/approximateScale);
    // Compress the size scale: thousands of games in one region must not shrink
    // every other region into overlapping labels. Counts remain exact in the UI.
    const nodes=states.map(s=>({id:s.id,ax:s.col*pitch+30,ay:s.row*pitch+30,x:s.col*pitch+30,y:s.row*pitch+30,side:weighted?(s.games.length?52*Math.sqrt(Math.log2(s.games.length+1)):emptySide):52}));
    function collisions() {
      for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
        const a=nodes[i],b=nodes[j],dx=b.x-a.x,dy=b.y-a.y;
        const min=(a.side+b.side)/2+7,overX=min-Math.abs(dx),overY=min-Math.abs(dy);
        if(overX>0 && overY>0){
          if(overX<overY){const move=overX/2+.01,sign=dx>=0?1:-1;a.x-=move*sign;b.x+=move*sign;}
          else {const move=overY/2+.01,sign=dy>=0?1:-1;a.y-=move*sign;b.y+=move*sign;}
        }
      }
    }
    for(let i=0;weighted&&i<150;i++){
      nodes.forEach(n=>{n.x+=(n.ax-n.x)*.035;n.y+=(n.ay-n.y)*.035;});
      collisions();
    }
    for(let i=0;weighted&&i<40;i++)collisions();
    const minX=Math.min(...nodes.map(n=>n.x-n.side/2)),maxX=Math.max(...nodes.map(n=>n.x+n.side/2));
    const minY=Math.min(...nodes.map(n=>n.y-n.side/2)),maxY=Math.max(...nodes.map(n=>n.y+n.side/2));
    const scale=(width-24)/(maxX-minX),height=(maxY-minY)*scale+24;
    return {width,height,nodes:nodes.map(n=>({...n,x:12+(n.x-minX)*scale,y:12+(n.y-minY)*scale,side:n.side*scale}))};
  }
  function navigate(states,current,direction) {
    const a=states.find(s=>s.id===current);
    const horizontal=direction==='left'||direction==='right';
    const sign=direction==='left'||direction==='up'?-1:1;
    const choices=states.filter(s=>s.id!==current).map(s=>{
      const along=(horizontal?s.col-a.col:s.row-a.row)*sign;
      const across=Math.abs(horizontal?s.row-a.row:s.col-a.col);
      return {s,along,score:Math.hypot(along,across)+across*1.8};
    }).filter(v=>v.along>0).sort((a,b)=>a.score-b.score||a.s.id.localeCompare(b.s.id));
    return choices.length?choices[0].s.id:current;
  }
  function nextLive(states,current,direction) {
    const live=states.filter(s=>s.games.length);
    if(!live.length)return current;
    const index=live.findIndex(s=>s.id===current);
    return live[(index<0?(direction>0?0:live.length-1):(index+direction+live.length)%live.length)].id;
  }
  return {layout,navigate,nextLive};
})();
