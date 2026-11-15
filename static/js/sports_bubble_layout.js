// Give game indices a predictable visual order, including near-vertical columns.
function leftToRight(points){return [...points].sort((a,b)=>Math.round(a.x)-Math.round(b.x)||a.y-b.y);}
function layoutBubbles(width,height,anchor,count){
 const size=width<620?112:124,pad=size/2+10,gap=size+14;
 height=Math.max(height,320);
 const hub={x:anchor.x,y:anchor.y},positions=[];
 if(width<620){
  const columns=count===1?1:2,start=(anchor.mapHeight||Math.min(height,240))+size/2+20;
  for(let i=0;i<count;i++)positions.push({x:width/2+(i%columns-(columns-1)/2)*(size+16),y:start+Math.floor(i/columns)*(size+16)});
  return {hub,size,height:Math.max(height,positions.length?positions.at(-1).y+pad:height),positions:leftToRight(positions)};
 }
 const candidates=[];
 function add(x,y){const half=(anchor.side||40)/2,dx=Math.max(0,Math.abs(x-hub.x)-half),dy=Math.max(0,Math.abs(y-hub.y)-half);if(x>=pad&&x<=width-pad&&y>=pad&&y<=height-pad&&Math.hypot(dx,dy)>=size/2+12)candidates.push({x,y});}
 // A fan around the selected state, followed by a grid for tight viewports.
 for(let radius=gap;radius<=Math.hypot(width,height);radius+=gap*.65){
  for(let i=0;i<32;i++){const angle=-Math.PI/2+i*Math.PI/16;add(hub.x+Math.cos(angle)*radius,hub.y+Math.sin(angle)*radius);}
 }
 for(let y=pad;y<=height-pad;y+=gap)for(let x=pad;x<=width-pad;x+=gap)add(x,y);
 for(let i=0;i<count;i++){
  const available=candidates.filter(p=>positions.every(q=>Math.hypot(p.x-q.x,p.y-q.y)>=gap));
  available.sort((a,b)=>Math.hypot(a.x-hub.x,a.y-hub.y)-Math.hypot(b.x-hub.x,b.y-hub.y));
  if(!available.length){
   // Make room vertically rather than clipping a game on a small phone.
   const y=height+pad;positions.push({x:pad+(i%2)*gap,y});height=y+pad;
  }else positions.push(available[0]);
 }
 return {hub,size,height,positions:leftToRight(positions)};
}
function neighbor(points,index,direction){
 const current=points[index],horizontal=['left','right'].includes(direction),sign=['left','up'].includes(direction)?-1:1;
 const candidates=points.map((p,i)=>({p,i,along:(horizontal?p.x-current.x:p.y-current.y)*sign,across:Math.abs(horizontal?p.y-current.y:p.x-current.x)})).filter(v=>v.i!==index&&v.along>1);
 candidates.sort((a,b)=>(Math.hypot(a.along,a.across)+a.across*1.5)-(Math.hypot(b.along,b.across)+b.across*1.5));
 return candidates[0]?.i??index;
}
if(typeof module!=='undefined')module.exports={layoutBubbles,neighbor};
