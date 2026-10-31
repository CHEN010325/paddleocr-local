"""Real HTTP/model acceptance of all official OvisOCR3 tasks; no inference stubs.

Run against a running workbench (default), or --url http://localhost:8084 --endpoint /ocr.
Fixtures, full model responses and per-task semantic checks are retained under --output.
"""
import argparse
import io
import json
import os
from pathlib import Path
import time

import fitz
import httpx
from PIL import Image, ImageDraw, ImageFont


def font(size):
    for name in ('C:/Windows/Fonts/msyh.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def image_file(folder, name, size, draw):
    image=Image.new('RGB',size,'white')
    draw(ImageDraw.Draw(image))
    path=folder/name
    image.save(path)
    return path


def fixtures(folder):
    folder.mkdir(parents=True,exist_ok=True)
    text=image_file(folder,'words.png',(800,300),lambda d:d.text((70,100),'HELLO WORLD',font=font(64),fill='black'))
    mixed=image_file(folder,'mixed.png',(900,400),lambda d:d.multiline_text((60,80),'HELLO WORLD\n中文测试',font=font(56),fill='black',spacing=32))
    translation=image_file(folder,'translation.png',(800,250),lambda d:d.text((70,80),'Hello world',font=font(64),fill='black'))
    def draw_invoice(d):
        d.text((60,45),'INVOICE',font=font(56),fill='black')
        for y,line in [(145,'Invoice No: ALPHA-2026'),(215,'Date: 2026-11-01'),(285,'Seller: Example Company'),(355,'Customer: Alice')]:
            d.text((60,y),line,font=font(32),fill='black')
        for y,row in [(470,['Item','Qty','Amount']),(545,['Notebook','2','20.00']),(620,['Pen','3','15.00'])]:
            d.rectangle((55,y-5,940,y+65),outline='black',width=2)
            for x,value in zip([70,520,720],row): d.text((x,y+6),value,font=font(30),fill='black')
            for x in [500,700]:d.line((x,y-5,x,y+65),fill='black',width=2)
        d.text((60,830),'Total: 35.00',font=font(44),fill='black')
    invoice=image_file(folder,'invoice.png',(1000,1100),draw_invoice)
    def draw_formula(d):
        d.text((80,100),'E = mc',font=font(78),fill='black')
        d.text((355,80),'2',font=font(48),fill='black')
    formula=image_file(folder,'formula.png',(650,300),draw_formula)
    def draw_chart(d):
        d.text((170,20),'SALES',font=font(36),fill='black')
        d.line((90,100,90,420,600,420),fill='black',width=3)
        d.rectangle((180,260,290,420),fill='#537bd4');d.rectangle((380,100,490,420),fill='#537bd4')
        for xy,value in [((215,220),'10'),((415,60),'20'),((220,435),'A'),((420,435),'B'),((180,490),'Source: Test')]:
            d.text(xy,value,font=font(28),fill='black')
    chart=image_file(folder,'chart.png',(700,550),draw_chart)
    shape=image_file(folder,'shape.png',(500,500),lambda d:d.ellipse((100,100,400,400),fill='red'))
    def draw_figure(d):
        d.text((50,40),'DOCUMENT WORKFLOW',font=font(38),fill='black')
        d.text((50,115),'Figure 1. A processing pipeline.',font=font(27),fill='black')
        for y,label in [(250,'Input'),(450,'OCR'),(650,'Markdown')]:
            d.rectangle((180,y,620,y+100),outline='black',width=4)
            d.text((260,y+22),label,font=font(38),fill='black')
            if y<650:
                d.line((400,y+100,400,y+190),fill='black',width=5)
                d.polygon([(388,y+172),(412,y+172),(400,y+190)],fill='black')
    figure=image_file(folder,'figure.png',(800,900),draw_figure)
    pdf=fitz.open()
    for n,rows in enumerate([[('Alpha','10'),('Beta','20')],[('Gamma','30'),('Delta','40')]]):
        page=pdf.new_page(width=600,height=800)
        page.insert_text((50,45),'INVENTORY' if n==0 else 'INVENTORY (continued)',fontsize=22)
        top=580 if n==0 else 70
        for index,row in enumerate([('Product','Amount')]+rows):
            y=top+index*60
            page.draw_rect(fitz.Rect(50,y,550,y+60),color=(0,0,0),width=1)
            page.draw_line(fitz.Point(360,y),fitz.Point(360,y+60))
            page.insert_text((65,y+38),row[0],fontsize=20)
            page.insert_text((380,y+38),row[1],fontsize=20)
    cross=folder/'crosspage.pdf';pdf.save(cross);pdf.close()
    return dict(text=text,mixed=mixed,translation=translation,invoice=invoice,formula=formula,chart=chart,shape=shape,figure=figure,cross=cross)


def cases(f):
    return [
        ('document_structured',f['invoice'],{},['ALPHA-2026','35.00']),
        ('document_markdown',f['invoice'],{},['ALPHA-2026','35.00']),
        ('document_layout',f['invoice'],{},[]),
        ('plain_ocr',f['mixed'],{},['HELLO','WORLD','中文测试']),
        ('word_spotting',f['text'],{},['HELLO','WORLD']),
        ('reference_grounding',f['text'],{'references':['HELLO','WORLD']},['HELLO','WORLD']),
        ('region_ocr',f['invoice'],{'region':[0,115,999,180]},['ALPHA-2026']),
        ('formula_recognition',f['formula'],{},['E','m','c']),
        ('chart_parsing',f['chart'],{},['10','20','A','B']),
        ('text_translation',f['translation'],{'targetLanguage':'Chinese','region':[0,0,999,999]},['你好','世界']),
        ('image_classification',f['shape'],{'targetDescription':'the main colored shape','candidateLabels':['red circle','blue square','green triangle']},['red circle']),
        ('visual_question_answering',f['invoice'],{'question':'What is the total amount on this invoice?'},['35.00']),
        ('position_aware_vqa',f['invoice'],{'question':'What is the total amount?'},['35.00']),
        ('kie_open',f['invoice'],{},['ALPHA-2026','35.00']),
        ('kie_fields',f['invoice'],{'fields':['invoice_number','total']},['ALPHA-2026','35.00']),
        ('kie_schema',f['invoice'],{'schema':{'invoice_number':'','seller':{'name':''},'total':''}},['ALPHA-2026','Example Company','35.00']),
        ('figure_captioning',f['figure'],{},['<caption>']),
        ('crosspage_table',f['cross'],{},['Alpha','Beta','Gamma','Delta']),
    ]


def run(args):
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    data=fixtures(output/'fixtures')
    headers={'Authorization':'Bearer '+os.environ['PANDOCR_API_TOKEN']} if os.getenv('PANDOCR_API_TOKEN') else {}
    report=[]
    with httpx.Client(base_url=args.url,timeout=args.timeout,headers=headers) as client:
        for task,path,options,expected in cases(data):
            if args.only and task not in args.only.split(','):continue
            started=time.monotonic();record={'task':task,'fixture':str(path),'expectedMarkers':expected}
            try:
                with path.open('rb') as file:
                    response=client.post(args.endpoint,data={'modelId':'ovisocr3','task':task,'taskOptions':json.dumps(options,ensure_ascii=False)},files={'file':(path.name,file,'application/pdf' if path.suffix=='.pdf' else 'image/png')})
                record['httpStatus']=response.status_code
                payload=response.json()
                (output/(task+'.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf8')
                text=json.dumps(payload.get('taskResults',payload),ensure_ascii=False)
                missing=[value for value in expected if value.lower() not in text.lower()]
                checks={'http200':response.status_code==200,'expectedMarkers':not missing}
                if task=='document_layout': checks['layoutBoxes']=bool(payload.get('taskResults',[[]])[0])
                if task=='formula_recognition':checks['latexDelimiters']='$$' in text
                if task=='crosspage_table':
                    enhanced=payload.get('enhancement',{})
                    checks.update(enhancementComplete=enhanced.get('status')=='complete',positiveMerge=bool(enhanced.get('crossPageTables')))
                record.update(checks=checks,missingMarkers=missing,passed=all(checks.values()))
            except Exception as exc:
                record.update(passed=False,error=str(exc))
            record['seconds']=round(time.monotonic()-started,3);report.append(record)
            print(json.dumps(record,ensure_ascii=True),flush=True)
            (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if all(record['passed'] for record in report) else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--endpoint',default='/api/ovisocr3')
    parser.add_argument('--output',default='tmp/ovis-real-acceptance/tasks')
    parser.add_argument('--only',default='')
    parser.add_argument('--timeout',type=float,default=600)
    raise SystemExit(run(parser.parse_args()))
