"""Generate the small category station badges (Pillow, no downloaded assets)."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SCALE = 8
COLORS = {
    'animation': '#995AE8', 'action-adventure': '#E56724',
    'comedy': '#D19A14', 'crime-mystery': '#336DCD',
    'documentary-history': '#298275', 'drama': '#BB4967',
    'family-kids': '#4B8B39', 'news-talk': '#2682A5',
    'reality-game-shows': '#AE6429', 'scifi-fantasy-horror': '#5F59CE',
}


def badge(category, color):
    image = Image.new('RGBA', (64*SCALE, 64*SCALE))
    d = ImageDraw.Draw(image)
    def box(coords): return tuple(round(n*SCALE) for n in coords)
    def line(points, width=3):
        d.line([box(p) for p in points], fill='white', width=width*SCALE, joint='curve')
    def ellipse(coords, fill=None, width=3):
        d.ellipse(box(coords), fill=fill, outline='white', width=width*SCALE)
    def arc(coords, start, end):
        d.arc(box(coords), start, end, fill='white', width=3*SCALE)
    d.rounded_rectangle(box((1,1,63,63)), radius=13*SCALE, fill=color)
    if category == 'animation':
        d.polygon([box(p) for p in [(29,12),(34,25),(47,30),(34,35),(29,48),(24,35),(11,30),(24,25)]], fill='white')
        line([(46,11),(46,21)],2);line([(41,16),(51,16)],2)
        ellipse((45,44,50,49), 'white', 1)
    elif category == 'action-adventure':
        d.polygon([box(p) for p in [(35,9),(15,35),(29,35),(25,55),(49,26),(35,26)]], fill='white')
    elif category in ('comedy', 'drama'):
        line([(14,16),(23,19),(41,19),(50,16),(48,35),(43,45),(32,52),(21,45),(16,35),(14,16)])
        arc((20,25,28,31),180,360);arc((36,25,44,31),180,360)
        if category == 'comedy': arc((23,30,41,44),0,180)
        else: arc((23,37,41,49),180,360)
    elif category == 'crime-mystery':
        ellipse((12,11,42,41),width=4);line([(39,39),(53,53)],5)
        arc((18,17,36,35),190,270)
    elif category == 'documentary-history':
        line([(32,19),(23,15),(12,15),(12,45),(23,45),(32,49),(41,45),(52,45),(52,15),(41,15),(32,19),(32,49)])
        for y in (25,32,39):
            line([(18,y),(25,y+1)],2);line([(39,y+1),(46,y)],2)
    elif category == 'family-kids':
        line([(10,29),(32,11),(54,29)],4)
        line([(17,25),(17,51),(47,51),(47,25)])
        ellipse((26,28,38,40),'white',1)
        line([(25,50),(25,47),(28,43),(36,43),(39,47),(39,50)],3)
    elif category == 'news-talk':
        d.rounded_rectangle(box((25,11,39,38)),radius=7*SCALE,outline='white',width=3*SCALE)
        arc((18,22,46,46),0,180);line([(18,29),(18,33)]);line([(46,29),(46,33)])
        line([(32,46),(32,53)]);line([(23,53),(41,53)])
        line([(26,21),(32,21)],2);line([(26,27),(32,27)],2)
    elif category == 'reality-game-shows':
        line([(21,13),(43,13),(43,28),(40,37),(32,42),(24,37),(21,28),(21,13)])
        line([(21,18),(12,18),(12,25),(16,31),(22,33)])
        line([(43,18),(52,18),(52,25),(48,31),(42,33)])
        line([(32,42),(32,51)]);line([(23,52),(41,52)],4)
    else:
        ellipse((18,17,46,45))
        # Wide tilted ring around a planet.
        line([(18,29),(10,34),(8,39),(12,43),(23,44),(37,39),(50,31),(56,25),(54,21),(47,21)])
        line([(48,9),(48,15)],2);line([(45,12),(51,12)],2)
        ellipse((13,51,16,54),'white',1)
    return image.resize((128,128),Image.Resampling.LANCZOS)


if __name__ == '__main__':
    destination=ROOT/'static/icons/categories'
    destination.mkdir(parents=True,exist_ok=True)
    for category,color in COLORS.items():
        badge(category,color).save(destination/(category+'.png'))
