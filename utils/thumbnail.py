from PIL import ImageDraw, ImageFont


def create_thumbnail(thumbnail, font_family, font_size, font_color, width, height, title):
    font = ImageFont.truetype(font_family + ".ttf", font_size)
    Xaxis = width - (width * 0.2)
    sizeLetterXaxis = font_size * 0.5
    XaxisLetterQty = round(Xaxis / sizeLetterXaxis)
    MarginYaxis = height * 0.12
    MarginXaxis = width * 0.05
    LineHeight = font_size * 1.1
    rgb = font_color.split(",")
    rgb = (int(rgb[0]), int(rgb[1]), int(rgb[2]))

    arrayTitle = []
    for word in title.split():
        if len(arrayTitle) == 0:
            arrayTitle.append(word)
        else:
            if len(arrayTitle[-1]) + len(word) < XaxisLetterQty:
                arrayTitle[-1] = arrayTitle[-1] + " " + word
            else:
                arrayTitle.append(word)

    draw = ImageDraw.Draw(thumbnail)
    for i in range(0, len(arrayTitle)):
        draw.text((MarginXaxis, MarginYaxis + (LineHeight * i)), arrayTitle[i], rgb, font=font)

    return thumbnail
