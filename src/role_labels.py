"""Offline Arabic category labels, not a claimed full-title translation."""
from .keywords import fold_text
LABELS = [
    ('عامل مخزن / تجهيز طلبات', ['warehouse','picker','magazyn','sklad','lager','kommission','sandel','raktar','noliktav','laotoo','magasin','almacen','magazzin','armazem']),
    ('تعبئة وتغليف', ['packer','packing','pakow','balic','verpack','emball','imball','pakir']),
    ('عامل إنتاج / مصنع', ['production','factory','produkc','produktions','vyrob','gamyb','termeles','produccion','produzione','producao']),
    ('تنظيف / تدبير غرف', ['clean','housekeep','cleaner','reinig','sprzat','uklid','uprat','valyt','takar','schoon','nettoy','limp','pulizi','rengor','stadare','siivoo']),
    ('عامل زراعي / حصاد', ['farm','harvest','agricultur','greenhouse','fruit pick','zbiory','szklarn','ernte','gewachs','serre','serra']),
    ('مساعد مطبخ / غسيل أطباق', ['kitchen','dishwash','kuchenhilfe','pomoc kuchen','aide de cuisine','aiuto cucina','ayudante de cocina','keukenhulp','afwasser']),
]

def arabic_role_label(record):
    title=fold_text(record.get('title') or '')
    for label, stems in LABELS:
        if any(stem in title for stem in stems):
            return 'تصنيف الدور بالعربية (ليس ترجمة كاملة): ' + label
    return 'ترجمة العنوان الكامل غير متاحة؛ راجع العنوان الأصلي'
