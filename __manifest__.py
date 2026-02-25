# -*- coding: utf-8 -*-
{
    'name': 'Stock Gap Analysis',
    'version': '18.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Analyse des écarts entre stock inventorié et ventes POS',
    'description': (
        'Compare le stock théorique (stock initial + réceptions − ventes POS) '
        'au stock réel pour détecter les anomalies et pertes non justifiées.'
    ),
    'depends': ['stock', 'point_of_sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/mat_stock_gap_views.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'auto_install': False,
    'application': False,
}
