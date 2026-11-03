<?php
/**
 * Static pages.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

get_header();

while ( have_posts() ) :
	the_post();
	get_template_part( 'template-parts/content', 'page' );

	if ( comments_open() || get_comments_number() ) {
		comments_template();
	}
endwhile;

get_footer();
