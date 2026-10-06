#ifndef NODEITEM_H
#define NODEITEM_H

#include <QColor>
#include <QGraphicsItem>

// Draws one MANET node: a coloured circle with its ID inside.
// The item's position (setPos) is the node centre in scene coordinates.
class NodeItem : public QGraphicsItem
{
public:
    NodeItem(int nodeId, const QColor &color, qreal radius);

    int nodeId() const;

    QRectF boundingRect() const override;
    void paint(QPainter *painter, const QStyleOptionGraphicsItem *option,
               QWidget *widget) override;

private:
    int m_nodeId;
    QColor m_color;
    qreal m_radius;
};

#endif // NODEITEM_H
