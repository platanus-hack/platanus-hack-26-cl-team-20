"""Isolated local Grounding DINO worker; no downloads or network access.

Uses the system Python with existing torch; small Transformers dependencies may
live in --packages outside the repository. It never changes the runtime.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

VOCABULARY={'painting':'picture','wall':'wall','pillow':'cushion','cushion':'cushion',
            'stuffed animal':'plush','stuffed toy':'plush','decorative mask':'mask',
            'plant':'plant','sofa':'sofa','stone column':'column','book':'book',
            'table':'table','screen':'screen','vase':'vase','lamp':'lamp',
            # escritorio de Max (smart desk, 2-nov): lo que hay sobre el mousepad
            'computer keyboard':'keyboard','keyboard':'keyboard','computer mouse':'mouse','mouse':'mouse',
            'game controller':'controller','gamepad':'controller','smartphone':'phone','cell phone':'phone',
            'human hand':'hand','hand':'hand','arm':'hand','mouse pad':'mousepad','cable':'cable',
            'midi controller':'instrument','tablet':'tablet','headphones':'headphones','remote control':'remote'}


def resolve_ambiguity(detections):
    """A similarly scored nested foreground label is not a confident identity.

    Backgrounds may contain objects. Two conflicting foreground descriptions
    of the same visible part remain unknown instead of stealing each other's
    pixels during composition. Keep the original label for inspection.
    """
    ambiguous=set()
    backgrounds={'wall','sofa','unknown'}
    for i,a in enumerate(detections):
        for j,b in enumerate(detections[:i]):
            if a['type']==b['type'] or {a['type'],b['type']}&backgrounds:continue
            aa,bb=a['box'],b['box']
            inter=max(0,min(aa[2],bb[2])-max(aa[0],bb[0]))*max(0,min(aa[3],bb[3])-max(aa[1],bb[1]))
            smaller=min((aa[2]-aa[0])*(aa[3]-aa[1]),(bb[2]-bb[0])*(bb[3]-bb[1]))
            if inter/max(1,smaller)>.85 and abs(a['score']-b['score'])<.08:
                ambiguous.update((i,j))
    return [{**d,**({'type':'unknown','ambiguous':True} if i in ambiguous else {})}
            for i,d in enumerate(detections)]


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('image');p.add_argument('--packages')
    p.add_argument('--checkpoint');p.add_argument('--threshold',type=float,default=.30)
    a=p.parse_args(argv)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    if a.packages:sys.path.insert(0,a.packages)
    cache=Path.home()/'.cache/huggingface/hub/models--IDEA-Research--grounding-dino-tiny'
    checkpoint=Path(a.checkpoint) if a.checkpoint else cache/'snapshots'/(cache/'refs/main').read_text().strip()
    if not (checkpoint/'model.safetensors').is_file():raise ValueError('Existing local DINO checkpoint required')
    import torch
    from PIL import Image
    from transformers import AutoProcessor,AutoModelForZeroShotObjectDetection
    torch.set_num_threads(3);started=time.perf_counter()
    processor=AutoProcessor.from_pretrained(checkpoint,local_files_only=True)
    model=AutoModelForZeroShotObjectDetection.from_pretrained(checkpoint,local_files_only=True).eval()
    image=Image.open(a.image).convert('RGB')
    inputs=processor(images=image,text='. '.join(VOCABULARY)+'.',return_tensors='pt')
    with torch.inference_mode():outputs=model(**inputs)
    result=processor.post_process_grounded_object_detection(outputs,inputs.input_ids,
        threshold=a.threshold,text_threshold=.22,target_sizes=[image.size[::-1]])[0]
    labels=result.get('text_labels',result.get('labels',[]));detections=[]
    for box,score,label in zip(result['boxes'],result['scores'],labels):
        kinds={kind for phrase,kind in VOCABULARY.items() if phrase in str(label).lower()}
        kind=next(iter(kinds)) if len(kinds)==1 else 'unknown'
        detections.append(dict(box=box.tolist(),score=float(score),label=str(label),type=kind))
    detections=resolve_ambiguity(detections)
    print(json.dumps(dict(status='ready',model='Grounding DINO tiny',revision=checkpoint.name,
                         backend='CPU',seconds=time.perf_counter()-started,detections=detections)))


if __name__=='__main__':main()
