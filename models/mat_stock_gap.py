# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MatStockGap(models.TransientModel):
    _name = 'mat.stock.gap'
    _description = 'Analyse Écart de Stock'

    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Entrepôt',
        required=True,
        default=lambda self: self.env['stock.warehouse'].search([], limit=1),
    )
    date_from = fields.Date(string='Date de début', required=True)
    date_to = fields.Date(
        string='Date de fin',
        required=True,
        default=fields.Date.today,
    )
    line_ids = fields.One2many(
        'mat.stock.gap.line', 'wizard_id', string='Résultats'
    )

    def action_compute(self):
        self.ensure_one()

        # Clear previous results
        self.line_ids.unlink()

        warehouse = self.warehouse_id
        lot_stock_id = warehouse.lot_stock_id.id
        date_from = self.date_from
        date_to = self.date_to

        # POS configs linked to this warehouse (via picking type)
        pos_configs = self.env['pos.config'].search([
            ('picking_type_id.warehouse_id', '=', warehouse.id)
        ])
        # Fallback to [0] so ANY(ARRAY[0]) never matches real orders
        pos_config_ids = pos_configs.ids if pos_configs else [0]

        self.env.cr.execute("""
            WITH RECURSIVE

            -- All stock locations belonging to this warehouse's main stock location
            loc_tree AS (
                SELECT id FROM stock_location WHERE id = %(lot_stock_id)s
                UNION ALL
                SELECT sl.id
                FROM stock_location sl
                JOIN loc_tree lt ON sl.location_id = lt.id
                WHERE sl.active = TRUE
            ),

            -- Stock on hand at the START of the period
            -- = all done moves up to (but NOT including) date_from
            stock_before AS (
                SELECT
                    sm.product_id,
                    COALESCE(SUM(
                        CASE WHEN sm.location_dest_id IN (SELECT id FROM loc_tree)
                             THEN sm.product_qty ELSE 0 END
                    ), 0)
                    - COALESCE(SUM(
                        CASE WHEN sm.location_id IN (SELECT id FROM loc_tree)
                             THEN sm.product_qty ELSE 0 END
                    ), 0) AS qty_start
                FROM stock_move sm
                WHERE sm.state = 'done'
                  AND sm.date::date < %(date_from)s
                  AND (
                      sm.location_dest_id IN (SELECT id FROM loc_tree)
                      OR sm.location_id    IN (SELECT id FROM loc_tree)
                  )
                GROUP BY sm.product_id
            ),

            -- Stock on hand at the END of the period
            -- = all done moves up to and INCLUDING date_to
            stock_after AS (
                SELECT
                    sm.product_id,
                    COALESCE(SUM(
                        CASE WHEN sm.location_dest_id IN (SELECT id FROM loc_tree)
                             THEN sm.product_qty ELSE 0 END
                    ), 0)
                    - COALESCE(SUM(
                        CASE WHEN sm.location_id IN (SELECT id FROM loc_tree)
                             THEN sm.product_qty ELSE 0 END
                    ), 0) AS qty_actual
                FROM stock_move sm
                WHERE sm.state = 'done'
                  AND sm.date::date <= %(date_to)s
                  AND (
                      sm.location_dest_id IN (SELECT id FROM loc_tree)
                      OR sm.location_id    IN (SELECT id FROM loc_tree)
                  )
                GROUP BY sm.product_id
            ),

            -- POS sales during the period (refunds have negative qty → auto-subtracted)
            pos_sales AS (
                SELECT
                    pol.product_id,
                    COALESCE(SUM(pol.qty), 0) AS qty_sold
                FROM pos_order_line pol
                JOIN pos_order po ON po.id = pol.order_id
                WHERE po.config_id = ANY(%(pos_config_ids)s)
                  AND po.state IN ('paid', 'done', 'invoiced')
                  AND po.date_order::date >= %(date_from)s
                  AND po.date_order::date <= %(date_to)s
                GROUP BY pol.product_id
            ),

            -- Validated incoming receipts during the period
            receipts AS (
                SELECT
                    sm.product_id,
                    COALESCE(SUM(sm.product_qty), 0) AS qty_received
                FROM stock_move sm
                JOIN stock_picking       sp  ON sp.id  = sm.picking_id
                JOIN stock_picking_type  spt ON spt.id = sp.picking_type_id
                WHERE spt.code = 'incoming'
                  AND spt.warehouse_id = %(warehouse_id)s
                  AND sm.state = 'done'
                  AND sm.date::date >= %(date_from)s
                  AND sm.date::date <= %(date_to)s
                GROUP BY sm.product_id
            )

            SELECT
                pp.id                                           AS product_id,
                pt.categ_id,
                COALESCE(sb.qty_start,   0)                     AS qty_start,
                COALESCE(ps.qty_sold,    0)                     AS qty_sold,
                COALESCE(r.qty_received, 0)                     AS qty_received,
                COALESCE(sa.qty_actual,  0)                     AS qty_actual,
                COALESCE(sb.qty_start,   0)
                    - COALESCE(ps.qty_sold,    0)
                    + COALESCE(r.qty_received, 0)               AS qty_theoretical,
                (   COALESCE(sb.qty_start,   0)
                    - COALESCE(ps.qty_sold,    0)
                    + COALESCE(r.qty_received, 0)
                ) - COALESCE(sa.qty_actual, 0)                  AS qty_gap

            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id

            LEFT JOIN stock_before sb ON sb.product_id = pp.id
            LEFT JOIN stock_after  sa ON sa.product_id = pp.id
            LEFT JOIN pos_sales    ps ON ps.product_id = pp.id
            LEFT JOIN receipts     r  ON r.product_id  = pp.id

            WHERE pt.type = 'consu'
              AND pt.is_storable = TRUE
              AND pp.active = TRUE
              AND pt.active = TRUE
              -- Only show products that had at least some movement
              AND (
                  COALESCE(sb.qty_start,   0) != 0
                  OR COALESCE(ps.qty_sold,    0) != 0
                  OR COALESCE(r.qty_received, 0) != 0
                  OR COALESCE(sa.qty_actual,  0) != 0
              )

            ORDER BY ABS(
                (   COALESCE(sb.qty_start,   0)
                    - COALESCE(ps.qty_sold,    0)
                    + COALESCE(r.qty_received, 0)
                ) - COALESCE(sa.qty_actual, 0)
            ) DESC NULLS LAST
        """, {
            'lot_stock_id': lot_stock_id,
            'date_from': date_from,
            'date_to': date_to,
            'pos_config_ids': pos_config_ids,
            'warehouse_id': warehouse.id,
        })

        rows = self.env.cr.dictfetchall()

        line_vals = [
            {
                'wizard_id': self.id,
                'product_id': row['product_id'],
                'categ_id': row['categ_id'],
                'qty_start': row['qty_start'],
                'qty_sold': row['qty_sold'],
                'qty_received': row['qty_received'],
                'qty_theoretical': row['qty_theoretical'],
                'qty_actual': row['qty_actual'],
                'qty_gap': row['qty_gap'],
            }
            for row in rows
        ]
        if line_vals:
            self.env['mat.stock.gap.line'].create(line_vals)

        # Reload the same form to display results
        return {
            'type': 'ir.actions.act_window',
            'name': 'Analyse Écart de Stock',
            'res_model': 'mat.stock.gap',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }


class MatStockGapLine(models.TransientModel):
    _name = 'mat.stock.gap.line'
    _description = 'Ligne Analyse Écart de Stock'
    # Preserve SQL ordering (biggest absolute gap first)
    _order = 'id'

    wizard_id = fields.Many2one(
        'mat.stock.gap', string='Analyse', ondelete='cascade'
    )
    product_id = fields.Many2one(
        'product.product', string='Produit', readonly=True
    )
    default_code = fields.Char(
        related='product_id.default_code',
        string='Référence',
        store=True,
        readonly=True,
    )
    categ_id = fields.Many2one(
        'product.category', string='Catégorie', readonly=True
    )
    qty_start = fields.Float(
        'Stock début', digits='Product Unit of Measure', readonly=True
    )
    qty_sold = fields.Float(
        'Ventes POS', digits='Product Unit of Measure', readonly=True
    )
    qty_received = fields.Float(
        'Réceptions', digits='Product Unit of Measure', readonly=True
    )
    qty_theoretical = fields.Float(
        'Stock théorique', digits='Product Unit of Measure', readonly=True
    )
    qty_actual = fields.Float(
        'Stock réel', digits='Product Unit of Measure', readonly=True
    )
    qty_gap = fields.Float(
        'Écart', digits='Product Unit of Measure', readonly=True
    )
