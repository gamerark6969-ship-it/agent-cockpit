"""Original illustrated sets and animated props for the CPU story renderer."""

import math


def scene_setting(scene: dict) -> str:
    explicit = scene.get("setting")
    if explicit in ("town", "forest", "space", "ocean", "room"):
        return explicit
    visual = str(scene.get("visual", "")).lower()
    for setting, keywords in (("forest", ("forest", "tree", "woods")), ("space", ("space", "moon", "planet")),
                              ("ocean", ("ocean", "sea", "boat", "island")), ("room", ("room", "inside", "bedroom"))):
        if any(word in visual for word in keywords):
            return setting
    return "town"


def draw_background(draw, scene: dict, t: float, width: int, height: int) -> None:
    setting = scene_setting(scene)
    base = {"space":(12,20,48), "forest":(20,65,61), "ocean":(19,74,107), "room":(70,53,82), "town":(41,46,87)}[setting]
    if "warm" in str(scene.get("palette", "")).lower():
        base = (82,41,58)
    step = max(1, height // 100)
    for y in range(0, height, step):
        color = tuple(min(255, int(c + y / height * 35)) for c in base)
        draw.rectangle((0,y,width,y+step), fill=color)
    if setting == "space":
        for i in range(38):
            x = ((i * 137.3 + t*2) % width)
            y = ((i * 73.1) % (height*.63))
            radius = max(1, width // 180)
            light = int(170 + 60*math.sin(t+i))
            draw.ellipse((x-radius,y-radius,x+radius,y+radius), fill=(light,light,230))
        draw.ellipse((width*.66,height*.26,width*.95,height*.26+width*.29),fill=(118,112,184))
        draw.arc((width*.60,height*.26+width*.07,width,height*.26+width*.2),0,360,fill=(205,185,232),width=max(1,width//70))
        draw.rectangle((0,height*.65,width,height),fill=(47,48,67))
    elif setting == "room":
        draw.rectangle((0,height*.22,width,height*.65),fill=(74,64,91))
        draw.rounded_rectangle((width*.08,height*.28,width*.4,height*.49),radius=width//40,fill=(41,111,140))
        draw.line((width*.24,height*.28,width*.24,height*.49),fill=(226,218,218),width=max(1,width//60))
        draw.line((width*.08,height*.38,width*.4,height*.38),fill=(226,218,218),width=max(1,width//60))
        draw.rectangle((0,height*.65,width,height),fill=(55,42,46))
        for i in range(7):
            draw.line((0,height*(.67+i*.045),width,height*(.67+i*.045)),fill=(70,53,55),width=2)
    elif setting == "ocean":
        draw.ellipse((width*.70,height*.25,width*.89,height*.25+width*.19),fill=(245,208,145))
        draw.rectangle((0,height*.50,width,height),fill=(27,92,125))
        for row in range(7):
            for i in range(5):
                x = ((i*width*.3 + t*(12+row*2)) % (width*1.3)) - width*.2
                y = height*(.54+row*.05)
                draw.arc((x,y,x+width*.25,y+width*.05),0,180,fill=(86,167,178),width=max(1,width//100))
        draw.polygon([(width*.12,height*.64),(width*.86,height*.64),(width*.70,height*.70),(width*.25,height*.70)],fill=(117,75,54))
    elif setting == "forest":
        draw.rectangle((0,height*.65,width,height),fill=(20,46,37))
        draw.polygon([(width*.45,height*.65),(width*.55,height*.65),(width*.9,height),(width*.1,height)],fill=(91,80,65))
        for i in range(7):
            x = (i*width*.2-width*.1) + math.sin(t*.3+i)*width*.004
            y = height*(.45-(i%3)*.035)
            draw.rectangle((x-width*.02,y,x+width*.02,height*.66),fill=(76,61,50))
            draw.polygon([(x-width*.12,y+height*.08),(x,y-height*.12),(x+width*.12,y+height*.08)],fill=(30+i%3*10,89+i%2*12,65))
    else:
        for i in range(9):
            x = i*width*.15-width*.1-t*width*.005
            top = height*(.4-(i%4)*.035)
            draw.rounded_rectangle((x,top,x+width*.12,height*.66),radius=width//90,fill=(31+i%3*12,62,88))
            for row in range(3):
                draw.rectangle((x+width*.025,top+height*(.025+row*.035),x+width*.075,top+height*(.04+row*.035)),fill=(167,157,126))
        draw.rectangle((0,height*.65,width,height),fill=(24,31,46))
        draw.line((0,height*.69,width,height*.69),fill=(68,79,91),width=max(1,width//120))


def draw_prop(draw, scene: dict, t: float, width: int, height: int) -> None:
    prop = scene.get("prop", "none")
    x, y, size = width*.80, height*.48, width*.10
    stroke = max(1,width//100)
    if prop == "clock":
        draw.ellipse((x-size,y-size,x+size,y+size),fill=(234,221,193),outline=(100,82,79),width=stroke)
        for length, rate in ((.65,.45),(.42,.08)):
            angle = t*rate-math.pi/2
            draw.line((x,y,x+size*length*math.cos(angle),y+size*length*math.sin(angle)),fill=(68,51,61),width=stroke)
    elif prop == "key":
        y += math.sin(t*1.7)*size*.15
        draw.ellipse((x-size*.8,y-size*.5,x-size*.1,y+size*.2),outline=(249,207,94),width=stroke*2)
        draw.line((x-size*.2,y-size*.1,x+size*.7,y+size*.6),fill=(249,207,94),width=stroke*2)
        draw.line((x+size*.5,y+size*.4,x+size*.7,y+size*.15),fill=(249,207,94),width=stroke*2)
    elif prop == "door":
        draw.rounded_rectangle((x-size,y-size*2.1,x+size,height*.66),radius=stroke*2,fill=(115,75,78),outline=(191,147,123),width=stroke)
        draw.ellipse((x+size*.5,y+size*.4,x+size*.7,y+size*.6),fill=(249,210,105))
    elif prop == "star":
        points = []
        for index in range(10):
            radius = size if index%2 == 0 else size*.43
            angle = index*math.pi/5-math.pi/2+t*.1
            points.append((x+radius*math.cos(angle),y+radius*math.sin(angle)))
        draw.polygon(points,fill=(250,213,112))
    elif prop == "bridge":
        draw.rectangle((width*.62,height*.61,width*.94,height*.65),fill=(154,107,69))
        for index in range(6):
            bx=width*(.63+index*.055)
            draw.line((bx,height*.54,bx,height*.65),fill=(212,172,108),width=stroke)
        draw.line((width*.63,height*.54,width*.91,height*.54),fill=(212,172,108),width=stroke)
    if scene.get("action") == "celebrate":
        for index in range(22):
            cx=(index*width*.173) % width
            cy=height*.3+(index*71+t*65) % (height*.37)
            draw.rectangle((cx,cy,cx+stroke*2,cy+stroke*4),fill=((249,200,97),(109,225,194),(200,140,224))[index%3])
