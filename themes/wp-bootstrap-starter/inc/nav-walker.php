<?php
/**
 * Minimal Bootstrap 5 nav walker.
 *
 * Top-level items with children become dropdown toggles; submenu items
 * render as dropdown-item links. (Bootstrap 5 has no native nested
 * dropdowns, so only one submenu level is styled.)
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

class WBS_Bootstrap_Nav_Walker extends Walker_Nav_Menu {

	/** @var int ID of the parent item whose submenu is being rendered. */
	protected $parent_item_id = 0;

	public function start_lvl( &$output, $depth = 0, $args = null ) {
		$indent = str_repeat( "\t", $depth );
		$output .= "\n{$indent}<ul class=\"dropdown-menu\" aria-labelledby=\"wbs-dropdown-{$this->parent_item_id}\">\n";
	}

	public function start_el( &$output, $item, $depth = 0, $args = null, $id = 0 ) {
		$indent       = str_repeat( "\t", $depth );
		$classes      = (array) $item->classes;
		$has_children = in_array( 'menu-item-has-children', $classes, true );
		$is_current   = in_array( 'current-menu-item', $classes, true )
			|| in_array( 'current-menu-ancestor', $classes, true );

		$li_classes   = array();
		$link_classes = array();
		$link_attrs   = '';

		if ( 0 === $depth ) {
			$li_classes[] = 'nav-item';
			$link_classes[] = 'nav-link';
			if ( $has_children ) {
				$li_classes[]       = 'dropdown';
				$link_classes[]     = 'dropdown-toggle';
				$this->parent_item_id = (int) $item->ID;
				$link_attrs = sprintf(
					' id="wbs-dropdown-%d" role="button" data-bs-toggle="dropdown" aria-expanded="false"',
					(int) $item->ID
				);
			}
		} else {
			$link_classes[] = 'dropdown-item';
		}

		if ( $is_current ) {
			$link_classes[] = 'active';
		}

		$output .= $indent . '<li class="' . esc_attr( implode( ' ', $li_classes ) ) . '">';
		$output .= sprintf(
			'<a class="%s" href="%s"%s>%s</a>',
			esc_attr( implode( ' ', $link_classes ) ),
			esc_url( $item->url ),
			$link_attrs,
			esc_html( $item->title )
		);
	}
}
