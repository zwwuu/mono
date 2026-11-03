<?php
/**
 * Category / tag / author / date archives.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

get_header();
?>
<header class="archive-header mb-4">
	<?php
	the_archive_title( '<h1 class="archive-title h3">', '</h1>' );
	the_archive_description( '<div class="archive-description text-muted">', '</div>' );
	?>
</header>

<?php
if ( have_posts() ) :
	while ( have_posts() ) :
		the_post();
		get_template_part( 'template-parts/content', get_post_type() );
	endwhile;
	wbs_pagination();
else :
	get_template_part( 'template-parts/content', 'none' );
endif;

get_footer();
