#include "NodeItem.h"

#include <QCursor>
#include <QFont>
#include <QPainter>

// The soft halo around the node is this many times the node radius
static const qreal HaloScale = 1.5;

NodeItem::NodeItem(int nodeId, const QColor &color, qreal radius)
    : m_nodeId(nodeId)
    , m_color(color)
    , m_radius(radius)
{
    setZValue(1); // nodes are drawn above the grid, ranges and links
    setFlag(QGraphicsItem::ItemIsSelectable);
    setCursor(Qt::PointingHandCursor);
    setToolTip(QString("Node %1").arg(nodeId));
}

int NodeItem::nodeId() const
{
    return m_nodeId;
}

QColor NodeItem::color() const
{
    return m_color;
}

QRectF NodeItem::boundingRect() const
{
    qreal r = m_radius * HaloScale + 1.0;
    return QRectF(-r, -r, 2 * r, 2 * r);
}

void NodeItem::paint(QPainter *painter, const QStyleOptionGraphicsItem *option, QWidget *widget)
{
    Q_UNUSED(option);
    Q_UNUSED(widget);

    painter->setRenderHint(QPainter::Antialiasing);

    // Halo
    QColor halo = m_color;
    halo.setAlpha(55);
    painter->setPen(Qt::NoPen);
    painter->setBrush(halo);
    painter->drawEllipse(QPointF(0, 0), m_radius * HaloScale, m_radius * HaloScale);

    // Selection ring
    if (isSelected()) {
        painter->setPen(QPen(QColor("#f8fafc"), 3));
        painter->setBrush(Qt::NoBrush);
        painter->drawEllipse(QPointF(0, 0), m_radius * 1.4, m_radius * 1.4);
    }

    // Body
    painter->setPen(QPen(m_color.lighter(140), 2));
    painter->setBrush(m_color);
    painter->drawEllipse(QPointF(0, 0), m_radius, m_radius);

    // Node ID
    QFont font = painter->font();
    font.setBold(true);
    font.setPixelSize(qRound(m_radius * 0.95));
    painter->setFont(font);
    painter->setPen(QColor("#0b1220"));
    QRectF body(-m_radius, -m_radius, 2 * m_radius, 2 * m_radius);
    painter->drawText(body, Qt::AlignCenter, QString::number(m_nodeId));
}
