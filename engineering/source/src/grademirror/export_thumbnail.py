"""Standard EXIF source thumbnails are not processed output.
Keep every photographic/private entry; unlink only the derived IFD1 image.
No pixel decoder, renderer or numerical array is imported here.
"""
import io,struct,hashlib
from .exif_structure import Graph,need,digest
POLICY='source-exif-v2';RULE='main-and-final-image-thumbnail-v1'
THUMBNAIL_POLICY='final-image-thumbnail-v1'
# Representation of the reduced image only, never main-image or camera tags.
IMAGE_TAGS={254,255,256,257,258,259,262,263,264,265,266,273,274,277,278,279,280,281,282,283,284,286,287,288,289,290,291,292,293,296,301,317,318,319,320,321,322,323,324,325,338,339,347,512,513,514,515,517,518,519,520,521,529,530,531,532,34675}
THUMB_PATH='IFD0/Next'
def photo_rows(graph, *, tiff=False):
 return [x for x in graph.semantic(tiff=tiff) if not (x['ifd']==THUMB_PATH and (x['tag'] in IMAGE_TAGS or x['tag']=='JPEGPreview'))]
def remove_source_thumbnail(raw):
 graph=Graph(io.BytesIO(raw),len(raw));rows=[r for r in graph.rows if r['ifd']==THUMB_PATH]
 if not rows:return raw,dict(policy_id=THUMBNAIL_POLICY,status='NO_SOURCE_STANDARD_THUMBNAIL',shell_source='FINAL_PRIMARY_IMAGE',removed=[],photographic_entries_preserved=True)
 # Multiple independent reduced pages are not silently flattened or relabelled.
 need(not any(r['ifd'].startswith(THUMB_PATH+'/Next') for r in graph.rows),'MULTIPLE_THUMBNAIL_DIRECTORIES_UNSUPPORTED')
 entries=[r for r in rows if r['tag']!='JPEGPreview'];keep=[r for r in entries if r['tag'] not in IMAGE_TAGS];removed=[dict(ifd=r['ifd'],tag=r['tag'],type=r['type'],count=r['count'],sha256=r['sha256']) for r in rows if r['tag'] in IMAGE_TAGS or r['tag']=='JPEGPreview']
 if not removed:return raw,dict(policy_id=THUMBNAIL_POLICY,status='NO_SOURCE_STANDARD_THUMBNAIL',shell_source='FINAL_PRIMARY_IMAGE',removed=[],photographic_entries_preserved=True)
 from .export_layout import compact_thumbnail
 out,compaction=compact_thumbnail(raw,graph,keep);after=Graph(io.BytesIO(out),len(out));need(photo_rows(graph)==photo_rows(after),'PHOTOGRAPHIC_METADATA_CHANGED')
 need(not any(r['ifd']==THUMB_PATH and (r['tag'] in IMAGE_TAGS or r['tag']=='JPEGPreview') for r in after.rows),'STALE_THUMBNAIL_STILL_LINKED')
 return out,dict(policy_id=THUMBNAIL_POLICY,status='SOURCE_THUMBNAIL_UNLINKED',shell_source='FINAL_PRIMARY_IMAGE',removed=removed,photographic_entries_preserved=True,private_payload_relocations=0,source_preview_bytes_not_exposed=True,compaction=compaction)
def verify_receipt(proof):
 need(isinstance(proof,dict) and proof.get('policy_id')==THUMBNAIL_POLICY and proof.get('shell_source')=='FINAL_PRIMARY_IMAGE' and proof.get('photographic_entries_preserved') is True,'THUMBNAIL_PROOF')
 need(proof.get('status') in ('SOURCE_THUMBNAIL_UNLINKED','NO_SOURCE_STANDARD_THUMBNAIL','SINGLE_PRIMARY_CONTAINER'),'THUMBNAIL_PROOF_STATE')
