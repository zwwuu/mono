<?php
/**
 * Full content layout for static pages.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;
?>
<article id="post-<?php the_ID(); ?>" <?php post_class(); ?>>
	<header class="entry-header mb-3">
		<?php the_title( '<h1 class="entry-title">', '</h1>' ); ?>
	</header>

	<?php if ( has_post_thumbnail() ) : ?>
		<figure class="entry-thumbnail mb-4">
			<?php the_post_thumbnail( 'wbs-featured', array( 'class' => 'img-fluid rounded' ) ); ?>
		</figure>
	<?php endif; ?>

	<div class="entry-content">
		<?php the_content(); ?>
	</div>
</article>
